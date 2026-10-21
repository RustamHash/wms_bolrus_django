from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.catalog.models import Caliber, Grade, Product, Supplier
from apps.catalog.services import load_refs
from apps.common.constants import TASK_WORK_STATUS
from apps.common.exceptions import WarehouseError
from apps.common.forms import text
from apps.common.http import done, guarded, roles_required
from apps.inventory.count import add_found, parse_fact_kg, save_count
from apps.inventory.models import InventoryDocument, InventoryLine, Movement
from apps.planning.models import Task
from apps.planning.views import task_context
from apps.storage.models import Pallet
from apps.storage.services import find_by_barcode


def _tasks(kind: str):
    return Task.objects.filter(kind=kind, status=TASK_WORK_STATUS[kind]).order_by("-id")


def _menu(user):
    from apps.accounts.access import screen_allows

    items = []
    if screen_allows(user, "tsd_receive", "view"):
        items.append({"digit": "1", "title": "Приёмка", "href": "/tsd/receive"})
    if screen_allows(user, "tsd_pick", "view"):
        items.append({"digit": "2", "title": "Отбор", "href": "/tsd/pick"})
    if screen_allows(user, "tsd_ship", "view"):
        items.append({"digit": "3", "title": "На отгрузку", "href": "/tsd/ship"})
    if screen_allows(user, "tsd_move", "view"):
        items.append({"digit": "4", "title": "Перемещение", "href": "/tsd/move"})
    if screen_allows(user, "tsd_find", "view"):
        items.append({"digit": "5", "title": "Поиск по штрихкоду", "href": "/tsd/find"})
    if screen_allows(user, "tsd_inventory", "view"):
        items.append({"digit": "6", "title": "Инвентаризация", "href": "/tsd/inventory"})
    if screen_allows(user, "tsd_shift", "view"):
        items.append({"digit": "9", "title": "Смена", "href": "/shift"})
    return items


@roles_required("receiver", "warehouse", tsd=("receive", "pick", "ship", "move"))
def tsd_menu(request):
    return render(request, "tsd/menu.html", {"items": _menu(request.user)})


@roles_required("receiver", tsd=("receive",))
def tsd_receive(request):
    return render(request, "tsd/tasks.html", {"title": "Приёмка", "tasks": _tasks("receive")})


@roles_required("warehouse", tsd=("pick",))
def tsd_pick(request):
    return render(request, "tsd/tasks.html", {"title": "Отбор", "tasks": _tasks("pick")})


@roles_required("warehouse", tsd=("ship",))
def tsd_ship(request):
    return render(request, "tsd/tasks.html", {"title": "На отгрузку", "tasks": _tasks("ship")})


@roles_required("office", tsd=("move",))
def tsd_move(request):
    code = (request.GET.get("barcode") or "").strip()
    pallet = _pallet_from_code(code) if code else None
    return render(
        request,
        "tsd/move.html",
        {"title": "Перемещение", "barcode": code, "pallet": pallet, "missing": bool(code) and pallet is None},
    )


@roles_required("office", tsd=("move",))
def tsd_move_save(request):
    back = "/tsd/move"

    def action():
        pallet = Pallet.objects.filter(pk=text(request.POST, "pallet_id")).first()
        if pallet is None:
            raise WarehouseError("Паллета не найдена")
        location = text(request.POST, "location")
        if not location:
            raise WarehouseError("Укажите ячейку")
        if len(location) > 120:
            raise WarehouseError("Ячейка длиннее 120 символов")
        units = pallet.units.all()
        if units.exists() and not units.exclude(status="shipped").exists():
            raise WarehouseError("Паллета уже отгружена")
        pallet.location = location
        pallet.save(update_fields=["location"])
        return pallet.number or pallet.barcode or f"#{pallet.id}"

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, f"Паллета {result} перемещена")


@roles_required("receiver", "warehouse", tsd=("receive", "pick", "ship"))
def tsd_task(request, task_id):
    task = Task.objects.select_related("supplier", "purchase_order", "order").filter(pk=task_id).first()
    if task is None:
        raise Http404
    if task.kind not in request.user.granted_access():
        messages.error(request, "Недостаточно прав")
        return redirect("/tsd")
    movements = list(
        Movement.objects.filter(task=task, kind="in")
        .select_related(
            "pallet__order__client",
            "pallet__reserved_client",
            "pallet__reserved_line__order",
            "lot__product",
            "lot__caliber",
            "lot__grade",
        )
        .prefetch_related("pallet__units__packaging")
        .order_by("-id")
    )
    return render(request, "tsd/task.html", task_context(task, movements))


@roles_required("receiver", "warehouse", tsd=("receive", "pick", "ship", "move"))
def tsd_find(request):
    code = request.GET.get("barcode") or ""
    kind, obj = find_by_barcode(code) if code else (None, None)
    movement = None
    if kind == "pallet":
        movement = Movement.objects.filter(pallet_id=obj.id, kind="in").first()
    elif kind == "unit" and obj.pallet_id:
        movement = Movement.objects.filter(pallet_id=obj.pallet_id, kind="in").first()
    return render(request, "tsd/find.html", {"barcode": code, "kind": kind, "obj": obj, "movement": movement, **load_refs()})


def _counting_documents():
    return InventoryDocument.objects.filter(status=InventoryDocument.Status.COUNTING).order_by("-doc_date", "-id")


def _pallet_from_code(code: str):
    kind, obj = find_by_barcode(code)
    if kind == "pallet":
        return obj
    if kind == "unit" and obj.pallet_id:
        return obj.pallet
    return None


@roles_required("receiver", "warehouse")
def tsd_inventory_list(request):
    return render(
        request,
        "tsd/inventory_list.html",
        {"title": "Инвентаризация", "documents": _counting_documents()},
    )


def _inventory_lines(document):
    rows = list(
        InventoryLine.objects.filter(document=document)
        .select_related(
            "pallet", "lot__product", "lot__caliber", "lot__grade", "lot__supplier", "supplier", "product", "caliber", "grade"
        )
        .order_by("id")
    )

    def label(row):
        product = row.lot.product if row.lot_id else row.product
        number = row.pallet.number if row.pallet_id else ""
        return ((product.name if product else "").casefold(), number)

    rows.sort(key=lambda row: (row.fact_kg is not None, *label(row)))
    return rows


@roles_required("receiver", "warehouse")
def tsd_inventory(request, doc_id):
    document = get_object_or_404(_counting_documents(), pk=doc_id)
    code = (request.GET.get("barcode") or "").strip()
    line_raw = (request.GET.get("line") or "").strip()
    lines = _inventory_lines(document)
    line = None
    missing = False
    if code:
        pallet = _pallet_from_code(code)
        if pallet is not None:
            line = next((row for row in lines if row.pallet_id == pallet.id), None)
        missing = line is None
    elif line_raw.isdigit():
        line = next((row for row in lines if row.id == int(line_raw)), None)
        missing = line is None
    return render(
        request,
        "tsd/inventory.html",
        {
            "title": document.number,
            "document": document,
            "barcode": code,
            "lines": lines,
            "line": line,
            "missing": missing,
            "suppliers": Supplier.objects.order_by("name"),
            "products": Product.objects.order_by("name"),
            "calibers": Caliber.objects.order_by("sort_order", "name"),
            "grades": Grade.objects.order_by("sort_order", "name"),
        },
    )


@roles_required("receiver", "warehouse")
def tsd_inventory_save(request, doc_id):
    document = get_object_or_404(_counting_documents(), pk=doc_id)
    back = f"/tsd/inventory/{document.id}"

    def action():
        line = InventoryLine.objects.filter(pk=request.POST.get("line_id"), document=document).first()
        if line is None:
            raise WarehouseError("Паллеты нет в этом документе")
        supplier_raw = text(request.POST, "supplier_id")
        try:
            supplier_id = int(supplier_raw)
        except ValueError as exc:
            raise WarehouseError("Выберите поставщика") from exc
        save_count(
            line,
            fact_kg=parse_fact_kg(text(request.POST, "fact_kg")),
            supplier_id=supplier_id,
            user=request.user,
            fact_given=True,
        )
        return line.pallet.number if line.pallet_id else (line.product.name if line.product_id else "новая")

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, f"Паллета {result} посчитана")


@roles_required("receiver", "warehouse")
def tsd_inventory_add(request, doc_id):
    document = get_object_or_404(_counting_documents(), pk=doc_id)
    back = f"/tsd/inventory/{document.id}"

    def action():
        def choice(name, label):
            raw = text(request.POST, name)
            try:
                return int(raw)
            except ValueError as exc:
                raise WarehouseError(f"Выберите {label}") from exc

        add_found(
            document,
            product_id=choice("product_id", "товар"),
            caliber_id=choice("caliber_id", "калибр"),
            grade_id=choice("grade_id", "сорт"),
            supplier_id=choice("supplier_id", "поставщика"),
            location=text(request.POST, "location"),
            qty_kg=parse_fact_kg(text(request.POST, "fact_kg")),
            user=request.user,
        )
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Паллета добавлена. На остаток встанет после проводки")
