from django.shortcuts import get_object_or_404, redirect, render

from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, parse_date, parse_kg, require_int, text
from apps.common.http import done, guarded, roles_required
from apps.inventory.models import Balance
from apps.planning.models import Task
from apps.planning.services import require_assigned
from apps.receiving.models import Lot
from apps.receiving.services import accept_loose, accept_pallet, delete_inbound, edit_inbound


def _category_ids(form) -> list[int]:
    found = []
    for raw in form.getlist("category_id"):
        try:
            found.append(int(raw))
        except (TypeError, ValueError):
            continue
    return found


def _accept_fields(form):
    manual = form.get("net_manual") == "1"
    return dict(
        packaging_id=require_int(form, "packaging_id", "Упаковка"),
        places=require_int(form, "places", "Количество упаковок"),
        pallet_type=text(form, "pallet_type"),
        gross_kg=parse_kg(form.get("gross"), "Грязный вес"),
        net_kg=parse_kg(form.get("net"), "Чистый вес") if manual else None,
        net_manual=manual,
        caliber_id=require_int(form, "caliber_id", "Калибр"),
        grade_id=require_int(form, "grade_id", "Сортность"),
        reserve=form.get("reserve") == "1",
    )


def receipts_redirect(request, receipt_id=None):
    return redirect("/tasks?kind=receive")


@roles_required("receiver", tsd=("receive",))
def task_receive(request, task_id):
    form = request.POST
    back = text(form, "next") or f"/tasks/{task_id}"

    def action():
        task = Task.objects.filter(pk=task_id).first()
        if task is None:
            raise WarehouseError("Задание не найдено")
        require_assigned(task, request.user)
        if text(form, "action") == "loose":
            rows = []
            for raw in form.getlist("line"):
                try:
                    line_id = int(raw)
                except ValueError:
                    continue
                qty_raw = (form.get(f"qty-{line_id}") or "").strip()
                if not qty_raw:
                    continue
                rows.append(
                    {
                        "task_line_id": line_id,
                        "caliber_id": require_int(form, f"caliber-{line_id}", "Калибр"),
                        "grade_id": require_int(form, f"grade-{line_id}", "Сортность"),
                        "qty_kg": parse_kg(qty_raw, "Количество"),
                    }
                )
            return accept_loose(task_id=task_id, rows=rows, user=request.user)
        return accept_pallet(
            task_line_id=require_int(form, "task_line_id", "Строка задания"),
            user=request.user,
            consignee_id=optional_int(form, "consignee_id"),
            category_ids=_category_ids(form),
            **_accept_fields(form),
        )

    result, response = guarded(request, back, action)
    if response:
        return response
    if text(form, "action") == "loose":
        return done(request, back, "Принято без паллета: " + ", ".join(result))
    pallet = result[0]
    return done(request, back, f"Паллета {pallet.number} принята. Паспорт печатается")


@roles_required("receiver")
def movement_edit(request, movement_id):
    form = request.POST
    back = text(form, "next") or "/tasks?kind=receive"

    def action():
        return edit_inbound(movement_id, **_accept_fields(form))

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Приход изменён, остаток пересчитан")


@roles_required("receiver", tsd=("receive",))
def movement_delete(request, movement_id):
    form = request.POST
    back = text(form, "next") or "/tasks?kind=receive"

    def action():
        delete_inbound(movement_id)

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Ошибочный приход удалён, остаток возвращён")


def _lot_page(lot: Lot) -> dict:
    balance = Balance.objects.filter(lot=lot).first()
    children = list(lot.children.select_related("product", "grade").order_by("id"))
    made = list(lot.transform_outputs.select_related("document").order_by("id"))
    spent = list(lot.transform_inputs.select_related("document", "pallet").order_by("id"))
    return {
        "title": lot.number or "Партия",
        "lot": lot,
        "balance": balance,
        "children": children,
        "made": made,
        "spent": spent,
    }


@roles_required("receiver", "warehouse")
def lot_passport(request, lot_id):
    lot = get_object_or_404(
        Lot.objects.select_related(
            "product", "caliber", "grade", "supplier", "receipt", "parent"
        ),
        pk=lot_id,
    )
    return render(request, "lots/passport.html", _lot_page(lot))


@roles_required("receiver", "warehouse")
def lot_passport_save(request, lot_id):
    lot = get_object_or_404(Lot, pk=lot_id)
    back = f"/lots/{lot.id}"

    def action():
        raw = text(request.POST, "expires_on")
        grade = text(request.POST, "passport_grade")
        supplier = text(request.POST, "passport_supplier")
        if len(grade) > 120:
            raise WarehouseError("Сорт с паспорта длиннее 120 символов")
        if len(supplier) > 200:
            raise WarehouseError("Поставщик с паспорта длиннее 200 символов")
        lot.expires_on = parse_date(raw, "Срок годности") if raw else None
        lot.passport_grade = grade
        lot.passport_supplier = supplier
        lot.save(update_fields=["expires_on", "passport_grade", "passport_supplier"])
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Паспорт партии сохранён")
