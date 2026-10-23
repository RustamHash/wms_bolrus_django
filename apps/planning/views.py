from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.db import close_old_connections, transaction
from django.db.models import Count, DecimalField, Exists, OuterRef, Prefetch, Q, Sum, Value, prefetch_related_objects
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.services import person_ids
from apps.catalog.models import Consignee, ConsigneeCategory
from apps.catalog.services import load_refs
from apps.common.constants import TASK_STATUS, task_status_choices
from apps.common.exceptions import WarehouseError
from apps.common.kg import as_kg
from apps.common.forms import optional_int, optional_int_query, parse_date, parse_kg, query_date, require_int, text
from apps.common.periods import range_from_query
from apps.common.http import back_url, done, guarded, roles_required
from apps.common.notices import notify
from apps.inventory.models import Movement
from apps.inventory.services import downstream_pallet_ids
from apps.logistics.models import Driver, Route, RouteOrder
from apps.picking.services import pick_ready_task, reserve_for_task
from apps.planning.models import Order, OrderLine, OrderRevision, PurchaseOrder, PurchaseOrderRevision, Task, TaskLine
from apps.planning.revisions import revision_summary
from apps.onec.exceptions import OnecError
from apps.onec.sync import iter_sync_customer_orders, iter_sync_purchase_orders, sync_customer_orders, sync_purchase_orders
from apps.planning.import_customer import ImportReport
from apps.planning.import_customer import import_customer_orders
from apps.planning.import_supplier import import_supplier_orders
from apps.planning.services import (
    close_task,
    close_task_order,
    create_receive_task,
    create_task,
    delete_customer_orders,
    delete_purchase_orders,
    delete_tasks,
    assign_receive_task,
    require_assigned,
    set_task_status,
    staff_for_tasks,
    unmapped_titles,
    posted_line_ids,
    order_pick_boards,
    received_fact_csv,
    save_customer_order,
    save_purchase_order,
    sum_kg,
)
from apps.receiving.services import pallet_defaults
from apps.shipping.services import waiting_pallets as ship_waiting


def _optional_date(raw, label):
    if not (raw or "").strip():
        return None
    return parse_date(raw, label)


def _purchase_lines(form):
    products = form.getlist("product")
    calibers = form.getlist("caliber")
    grades = form.getlist("grade")
    qtys = form.getlist("qty")
    codes = form.getlist("external_code")
    names = form.getlist("external_name")
    cards = form.getlist("external_product")
    units = form.getlist("unit")
    flags = form.getlist("for_task")
    customers = form.getlist("customer_line")
    plans = form.getlist("reserve_kg")
    rows = []
    width = max(len(products), len(calibers), len(grades), len(qtys), len(codes), len(customers))
    for index in range(width):
        def cell(values, blank=""):
            return (values[index] if index < len(values) else blank).strip()

        product = cell(products)
        code = cell(codes)
        if not product and not code:
            continue
        try:
            product_id = int(product) if product else None
            caliber = cell(calibers)
            grade = cell(grades)
            card = cell(cards)
            caliber_id = int(caliber) if caliber else None
            grade_id = int(grade) if grade else None
            external_product_id = int(card) if card else None
            customer_raw = cell(customers)
            customer_line_id = int(customer_raw) if customer_raw else None
        except ValueError as exc:
            raise WarehouseError("Некорректная строка заказа") from exc
        plan_raw = cell(plans)
        rows.append(
            {
                "product_id": product_id,
                "caliber_id": caliber_id,
                "grade_id": grade_id,
                "qty_kg": parse_kg(cell(qtys), "Количество"),
                "external_code": code,
                "external_name": cell(names),
                "external_product_id": external_product_id,
                "unit": cell(units) or "кг",
                "for_task": cell(flags, "1") != "0",
                "customer_line_id": customer_line_id,
                "reserve_kg": parse_kg(plan_raw, "План резерва") if plan_raw else None,
            }
        )
    return rows


def _customer_lines(form):
    ids = form.getlist("line_id")
    keeps = form.getlist("keep")
    products = form.getlist("product")
    calibers = form.getlist("caliber")
    grades = form.getlist("grade")
    qtys = form.getlist("qty")
    count = max(len(ids), len(keeps), len(products), len(calibers), len(grades), len(qtys))
    rows = []
    for index in range(count):
        def cell(values, blank=""):
            return (values[index] if index < len(values) else blank).strip()

        line_raw = cell(ids)
        product_raw = cell(products)
        if not line_raw and not product_raw:
            continue
        try:
            line_id = int(line_raw) if line_raw else None
            product_id = int(product_raw) if product_raw else None
            caliber_raw = cell(calibers)
            grade_raw = cell(grades)
            keep_caliber = caliber_raw == "range"
            keep_grade = grade_raw == "keep"
            caliber_id = int(caliber_raw) if caliber_raw and not keep_caliber else None
            grade_id = int(grade_raw) if grade_raw and not keep_grade else None
        except ValueError as exc:
            raise WarehouseError("Некорректная строка заказа") from exc
        rows.append(
            {
                "line_id": line_id,
                "keep": cell(keeps) == "1",
                "keep_caliber": keep_caliber,
                "keep_grade": keep_grade,
                "product_id": product_id,
                "caliber_id": caliber_id,
                "grade_id": grade_id,
                "qty_kg": parse_kg(cell(qtys), "Количество"),
            }
        )
    return rows


def _customer_line_choices(extra_ids=None):
    from django.db.models import Q

    from apps.common.kg import format_kg
    from apps.planning.models import OrderLine

    query = Q(order__status__in=("open", "partial"), for_task=True)
    if extra_ids:
        query |= Q(id__in=list(extra_ids))
    rows = (
        OrderLine.objects.filter(query)
        .select_related("order__consignee", "product", "external_product__alias__product")
        .order_by("order__ship_date", "order_id", "id")
    )
    return [
        {
            "id": line.id,
            "product_id": line.product_id or "",
            "label": f"{line.order.number} · {line.order.consignee.name} · {line.product_title()} · {format_kg(line.qty_kg)} кг",
        }
        for line in rows
    ]


def _purchase_printers():
    from apps.printing.models import Printer

    return list(Printer.objects.order_by("-is_default", "name"))


def _purchase_form(order, user):
    from apps.planning.reserve import holding_by_order_line, plan_kg

    unmapped = []
    linked = []
    lines = []
    if order is not None:
        lines = list(
            order.lines.select_related(
                "product",
                "caliber",
                "grade",
                "external_product",
                "customer_line__order__consignee",
                "customer_line__product",
                "customer_line__external_product__alias__product",
            )
        )
        for line in lines:
            if line.for_task and not line.product_id:
                unmapped.append(line)
            if line.customer_line_id:
                linked.append(line.customer_line_id)
    held = holding_by_order_line(linked)
    printers = _purchase_printers()
    default_printer_id = next((item.id for item in printers if item.is_default), printers[0].id if printers else None)
    return {
        "order": order,
        "purchase_lines": lines,
        "tasks": list(Task.objects.filter(purchase_order=order).order_by("-id")) if order else [],
        "editable": user.is_office and (order is None or order.status != "closed"),
        "can_status": user.is_office,
        "printers": printers,
        "default_printer_id": default_printer_id,
        "unmapped": unmapped,
        "customer_lines": _customer_line_choices(linked),
        "held": held,
        "plans": {line.id: plan_kg(line) for line in lines if line.customer_line_id},
        "today": date.today().isoformat(),
        **load_refs(),
    }


def _customer_page(order, user):
    from apps.planning.reserve import holding_by_order_line

    unmapped = []
    reserves = []
    if order is not None:
        lines = list(order.lines.all())
        held = holding_by_order_line([line.id for line in lines])
        for line in lines:
            if line.warehouse_product() is None:
                unmapped.append(line)
            kg = held.get(line.id)
            if kg:
                reserves.append({"title": line.product_title(), "kg": kg})
    refs = load_refs()
    party_grades = ""
    category_grade_id = None
    if order is not None and order.consignee_id:
        from apps.catalog.grade_policy import grade_for_consignee, grade_names, shipping_grades

        party = shipping_grades(order.consignee)
        if party:
            party_grades = grade_names(order.consignee)
            refs["grades"] = party
        category_grade = grade_for_consignee(order.consignee)
        if category_grade is not None:
            category_grade_id = category_grade.id
            if all(item.id != category_grade.id for item in refs["grades"]):
                refs["grades"] = [category_grade, *refs["grades"]]
    return {
        "order": order,
        "editable": user.is_office and (order is None or order.status != "closed"),
        "can_status": user.is_office,
        "posted_ids": posted_line_ids(order) if order else set(),
        "tasks": list(Task.objects.filter(order=order).order_by("-id")) if order else [],
        "unmapped": unmapped,
        "reserves": reserves,
        "party_grades": party_grades,
        "category_grade_id": category_grade_id,
        "today": date.today().isoformat(),
        **(order_pick_boards(order) if order else {}),
        **refs,
    }


def task_context(task, movements, user=None):
    allocations = []
    picked = {}
    if task.kind == "pick":
        from apps.picking.services import allocation_rows, picked_map

        allocations = allocation_rows(task)
        picked = picked_map(task)
        waiting = []
    elif task.kind == "ship":
        waiting = ship_waiting(task)
    else:
        waiting = []
    gone = downstream_pallet_ids(item.pallet_id for item in movements if item.kind == "in")
    editable = {item.id for item in movements if item.kind == "in" and item.pallet_id not in gone}
    from apps.planning.reserve import assigned_by_order_line, plan_kg

    prefetch_related_objects(
        [task],
        Prefetch(
            "lines",
            queryset=TaskLine.objects.select_related(
                "product",
                "caliber",
                "grade",
                "order_line__caliber_from",
                "order_line__caliber_to",
                "purchase_line__customer_line__order__consignee",
            ),
        ),
    )
    lines = list(task.lines.all())
    customer_ids = [
        line.purchase_line.customer_line_id
        for line in lines
        if line.purchase_line_id and line.purchase_line.customer_line_id
    ]
    assigned = assigned_by_order_line(customer_ids)
    reserve = {}
    for line in lines:
        customer = line.purchase_line.customer_line if line.purchase_line_id else None
        if customer is None:
            continue
        reserve[line.id] = {
            "label": f"{customer.order.number}, {customer.order.consignee.name}",
            "plan": format(plan_kg(line.purchase_line), "f"),
            "held": format(assigned.get(customer.id, Decimal("0")), "f"),
        }
    received_rows = (
        Movement.objects.filter(task_line_id__in=[line.id for line in lines], kind="in")
        .values("task_line_id")
        .annotate(total=Sum("qty_kg"))
    )
    received_totals = {row["task_line_id"]: as_kg(row["total"]) for row in received_rows}
    received = {line.id: received_totals.get(line.id, Decimal("0.000")) for line in lines}
    return {
        "task": task,
        "movements": movements,
        "waiting": waiting,
        "allocations": allocations,
        "picked": picked,
        "editable": editable,
        "received": received,
        "loose_lines": [{"line": line, "left": as_kg(line.qty_kg) - received[line.id]} for line in lines],
        "reserve": reserve,
        "consignees": list(Consignee.objects.order_by("name")) if task.kind == "receive" and not task.order_id else [],
        "consignee_categories": list(ConsigneeCategory.objects.order_by("sort_order", "name")) if task.kind == "receive" else [],
        "defaults": pallet_defaults(task) if task.kind == "receive" else {},
        "last_pallet": list(pallet_defaults(task).values())[0] if task.kind == "receive" and pallet_defaults(task) else None,
        "can_receive": task.kind == "receive" and task.is_working() and user is not None and (user.role == "receiver" or user.is_office),
        "can_status": user is not None and user.is_office,
        "status_choices": task_status_choices(task.kind, task.status),
        "receivers": staff_for_tasks([task], task.kind) if task.kind in ("receive", "pick", "ship") else [],
        **(load_refs() if task.kind == "receive" else {}),
    }


@roles_required("warehouse", "receiver")
def order_home(request):
    return render(
        request,
        "hub.html",
        {
            "title": "Заказы",
            "items": [
                {"href": "/orders/suppliers", "title": "Заказы поставщикам"},
                {"href": "/orders/customers", "title": "Заказы покупателей"},
            ],
        },
    )


@roles_required("warehouse", "receiver")
def supplier_orders(request):
    date_from = query_date(request.GET.get("date_from"))
    date_to = query_date(request.GET.get("date_to"))
    supplier_id = optional_int_query(request.GET.get("supplier_id"))
    status = request.GET.get("status")
    if status not in ("", "all", "draft", "incoming", "closed"):
        status = ""
    query = PurchaseOrder.objects.prefetch_related("lines").order_by("-doc_date", "-id")
    if date_from:
        query = query.filter(doc_date__gte=date_from)
    if date_to:
        query = query.filter(doc_date__lte=date_to)
    if supplier_id:
        query = query.filter(supplier_id=supplier_id)
    if status == "":
        query = query.exclude(status="closed")
    elif status != "all":
        query = query.filter(status=status)
    orders = list(query)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return render(
        request,
        "orders/suppliers.html",
        {
            "rows": [{"order": item, "kg": sum_kg(item.lines.all())} for item in orders],
            "suppliers": load_refs()["suppliers"],
            "date_from": request.GET.get("date_from") or "",
            "date_to": request.GET.get("date_to") or "",
            "supplier_id": supplier_id,
            "status": status,
            "import_from": tomorrow,
            "import_to": tomorrow,
        },
    )


CUSTOMER_SORTS = {
    "number": "number",
    "external": "external_number",
    "revision": "revision",
    "ship_date": "ship_date",
    "consignee": "consignee__name",
    "seller": "seller__name",
    "kg": "kg_total",
    "status": "status",
}


def _customer_ranges(params):
    legacy = "date_from" in params or "date_to" in params
    ship_asked = any((params.get(key) or "").strip() for key in ("ship_from_type", "ship_from", "ship_to_type", "ship_to"))
    if legacy and not ship_asked:
        ship_from = query_date(params.get("date_from")) if "date_from" in params else date.today()
        ship = {
            "from_type": "custom" if (params.get("date_from") or "").strip() else ("" if "date_from" in params else "today"),
            "from_value": (params.get("date_from") or "").strip(),
            "to_type": "custom" if (params.get("date_to") or "").strip() else "",
            "to_value": (params.get("date_to") or "").strip(),
            "from_date": ship_from,
            "to_date": query_date(params.get("date_to")),
        }
        if ship["from_type"] == "today":
            ship["from_date"] = date.today()
    else:
        ship = range_from_query(params, "ship", default_from="" if params else "today")
    return ship


def _order_driver(order) -> str:
    names = []
    for link in order.live_routes:
        if link.route.driver_id and link.route.driver.full_name not in names:
            names.append(link.route.driver.full_name)
    return ", ".join(names)


@roles_required("warehouse", "receiver")
def customer_orders(request):
    ship_range = _customer_ranges(request.GET)
    search = (request.GET.get("search") or "").strip()
    party = (request.GET.get("party") or "").strip()
    number = (request.GET.get("number") or "").strip()
    document = (request.GET.get("document") or "").strip()
    status = request.GET.get("status") or ""
    driver_id = optional_int_query(request.GET.get("driver"))
    sort = request.GET.get("sort") or "ship_date"
    direction = request.GET.get("dir") or "desc"
    if sort not in CUSTOMER_SORTS:
        sort = "ship_date"
    if direction not in ("asc", "desc"):
        direction = "desc"
    on_route = RouteOrder.objects.filter(order_id=OuterRef("pk")).exclude(route__status=Route.Status.CANCELLED)
    query = Order.objects.select_related("seller", "consignee__zone").prefetch_related(
        Prefetch(
            "route_orders",
            queryset=RouteOrder.objects.exclude(route__status=Route.Status.CANCELLED).select_related("route__driver"),
            to_attr="live_routes",
        )
    ).annotate(
        kg_total=Coalesce(
            Sum("lines__qty_kg", filter=Q(lines__for_task=True)),
            Value(Decimal("0.000")),
            output_field=DecimalField(max_digits=12, decimal_places=3),
        ),
    )
    if ship_range["from_date"]:
        query = query.filter(ship_date__gte=ship_range["from_date"])
    if ship_range["to_date"]:
        query = query.filter(ship_date__lte=ship_range["to_date"])
    if search:
        query = query.filter(
            Q(number__icontains=search)
            | Q(external_number__icontains=search)
            | Q(consignee__name__icontains=search)
            | Q(consignee__address__icontains=search)
            | Q(consignee__zone__name__icontains=search)
            | Q(seller__name__icontains=search)
            | Exists(on_route.filter(route__driver__full_name__icontains=search))
        )
    if party:
        query = query.filter(consignee__name__icontains=party)
    if number:
        query = query.filter(number__icontains=number)
    if document:
        query = query.filter(external_number__icontains=document)
    if status in ("open", "partial", "closed"):
        query = query.filter(status=status)
    if driver_id:
        query = query.filter(Exists(on_route.filter(route__driver_id=driver_id)))
    field = CUSTOMER_SORTS[sort]
    if direction == "desc":
        field = f"-{field}"
    orders = list(query.order_by(field, "-id"))
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return render(
        request,
        "orders/customers.html",
        {
            "rows": [{"order": item, "kg": item.kg_total, "driver": _order_driver(item)} for item in orders],
            "ship_range": ship_range,
            "search": search,
            "status": status if status in ("open", "partial", "closed") else "",
            "driver_id": driver_id,
            "drivers": list(Driver.objects.filter(is_active=True).order_by("full_name")),
            "sort": sort,
            "dir": direction,
            "import_from": tomorrow,
            "import_to": tomorrow,
        },
    )


@roles_required("office")
def supplier_order_new(request):
    return render(request, "orders/supplier.html", _purchase_form(None, request.user))


@roles_required("office")
def supplier_order_create(request):
    form = request.POST

    def action():
        return save_purchase_order(
            None,
            supplier_id=require_int(form, "supplier_id", "Поставщик"),
            doc_date=parse_date(form.get("doc_date"), "Дата"),
            receipt_date=_optional_date(form.get("receipt_date"), "Дата поступления"),
            status=text(form, "status") or "draft",
            comment=text(form, "comment"),
            lines=_purchase_lines(form),
            user=request.user,
            printer_id=optional_int(form, "printer_id"),
        )

    result, response = guarded(request, "/orders/suppliers/new", action)
    if response:
        return response
    return done(request, f"/orders/suppliers/{result.id}", "Записано")


@roles_required("warehouse", "receiver")
def supplier_order_detail(request, purchase_id):
    order = get_object_or_404(PurchaseOrder, pk=purchase_id)
    return render(request, "orders/supplier.html", _purchase_form(order, request.user))


@roles_required("office")
def supplier_order_save(request, purchase_id):
    form = request.POST

    def action():
        order = PurchaseOrder.objects.filter(pk=purchase_id).first()
        if order is None:
            raise WarehouseError("Заказ поставщику не найден")
        closed = order.status == "closed"
        return save_purchase_order(
            order,
            supplier_id=order.supplier_id if closed else require_int(form, "supplier_id", "Поставщик"),
            doc_date=order.doc_date if closed else parse_date(form.get("doc_date"), "Дата"),
            receipt_date=order.receipt_date if closed else _optional_date(form.get("receipt_date"), "Дата поступления"),
            status=text(form, "status") or order.status,
            comment=order.comment if closed else text(form, "comment"),
            lines=[] if closed else _purchase_lines(form),
            user=request.user,
            printer_id=None if closed else optional_int(form, "printer_id"),
        )

    result, response = guarded(request, f"/orders/suppliers/{purchase_id}", action)
    if response:
        return response
    return done(request, f"/orders/suppliers/{purchase_id}", "Записано")


@roles_required("office")
def supplier_order_task(request, purchase_id):
    def action():
        order = PurchaseOrder.objects.filter(pk=purchase_id).first()
        if order is None:
            raise WarehouseError("Заказ поставщику не найден")
        return create_receive_task(order, request.user)

    result, response = guarded(request, f"/orders/suppliers/{purchase_id}", action)
    if response:
        return response
    return done(request, f"/tasks/{result.id}", "Задание записано по заказу")


@roles_required("warehouse", "receiver")
def supplier_order_fact(request, purchase_id):
    order = get_object_or_404(PurchaseOrder, pk=purchase_id)
    try:
        content = received_fact_csv(order)
    except WarehouseError as exc:
        return _error(request, f"/orders/suppliers/{purchase_id}", str(exc))
    filename = quote(f"{order.number}-факт.csv")
    return HttpResponse(
        content,
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _error(request, url, message):
    from django.contrib import messages

    messages.error(request, message)
    return redirect(url)


def _onec_import_params(request, *, back: str):
    from apps.onec.sync import delivery_period

    update = request.POST.get("update") == "1"
    by_number = request.POST.get("by_number") == "1"
    number = (request.POST.get("order_number") or "").strip() if by_number else ""
    if by_number and not number:
        raise WarehouseError("Укажите номер заказа 1С")
    date_from = date_to = None
    if not by_number:
        date_from, date_to = delivery_period(
            query_date(request.POST.get("date_from")),
            query_date(request.POST.get("date_to")),
        )
    return update, by_number, number, date_from, date_to


def _onec_stream(iterator):
    """Потоковая отдача журнала импорта (по строке).

    Каждая строка дополняется паддингом ~2 КиБ — иначе runserver/прокси
    буферизуют мелкие чанки и лог появляется только в конце.
    """

    pad = " " * 2048

    def chunk(text: str) -> bytes:
        return (text.rstrip("\n") + "\n" + pad + "\n").encode("utf-8")

    def generate():
        close_old_connections()
        try:
            yield chunk("Импорт начат…")
            for item in iterator:
                if isinstance(item, ImportReport):
                    created = len(item.created)
                    updated = len(item.updated)
                    yield chunk(
                        f"Итог: создано {created}, обновлено {updated}, "
                        f"без изменений {len(item.unchanged)}"
                    )
                    continue
                yield chunk(str(item))
        except (OnecError, WarehouseError) as exc:
            yield chunk(f"Ошибка: {exc}")
        except Exception as exc:  # noqa: BLE001
            yield chunk(f"Ошибка: {exc}")
        finally:
            close_old_connections()

    response = StreamingHttpResponse(generate(), content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-cache, no-store"
    response["X-Accel-Buffering"] = "no"
    return response


@roles_required("office")
def customer_orders_import(request):
    """Импорт заказов покупателей из 1С по периоду отгрузки или по номеру."""
    try:
        update, by_number, number, date_from, date_to = _onec_import_params(request, back="/orders/customers")
    except WarehouseError as exc:
        return _error(request, "/orders/customers", str(exc))

    if request.headers.get("X-Onec-Stream") == "1":
        return _onec_stream(
            iter_sync_customer_orders(
                request.user,
                update=update,
                date_from=date_from,
                date_to=date_to,
                number=number or None,
            )
        )

    def action():
        try:
            return sync_customer_orders(
                request.user,
                update=update,
                date_from=date_from,
                date_to=date_to,
                number=number or None,
            )
        except OnecError as exc:
            raise WarehouseError(str(exc)) from exc

    result, response = guarded(request, "/orders/customers", action)
    if response:
        return response
    if by_number:
        title = f"Импорт из 1С · номер {number}"
    else:
        title = f"Импорт из 1С · отгрузка {date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}"
    return render(
        request,
        "orders/import_result.html",
        {
            "report": result,
            "title": title,
            "list_href": "/orders/customers",
            "party_label": "Грузополучатель",
            "item_href": "/orders/",
        },
    )


@roles_required("office")
def supplier_orders_import(request):
    """Импорт заказов поставщикам из 1С по периоду поступления или по номеру."""
    try:
        update, by_number, number, date_from, date_to = _onec_import_params(request, back="/orders/suppliers")
    except WarehouseError as exc:
        return _error(request, "/orders/suppliers", str(exc))

    if request.headers.get("X-Onec-Stream") == "1":
        return _onec_stream(
            iter_sync_purchase_orders(
                request.user,
                update=update,
                date_from=date_from,
                date_to=date_to,
                number=number or None,
            )
        )

    def action():
        try:
            return sync_purchase_orders(
                request.user,
                update=update,
                date_from=date_from,
                date_to=date_to,
                number=number or None,
            )
        except OnecError as exc:
            raise WarehouseError(str(exc)) from exc

    result, response = guarded(request, "/orders/suppliers", action)
    if response:
        return response
    if by_number:
        title = f"Импорт из 1С · номер {number}"
    else:
        title = f"Импорт из 1С · поступление {date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}"
    return render(
        request,
        "orders/import_result.html",
        {
            "report": result,
            "title": title,
            "list_href": "/orders/suppliers",
            "party_label": "Поставщик",
            "item_href": "/orders/suppliers/",
            "revisions_href": "/orders/suppliers/",
        },
    )


@roles_required("office")
def customer_orders_import_excel(request):
    upload = request.FILES.get("file")
    filename = getattr(upload, "name", "") or ""
    if not filename:
        return _error(request, "/service/imports", "Выберите файл шапки")
    if not filename.lower().endswith(".xlsx"):
        return _error(request, "/service/imports", "Нужен файл .xlsx")
    payload = upload.read()

    def action():
        return import_customer_orders(payload, request.user, update=request.POST.get("update") == "1")

    result, response = guarded(request, "/service/imports", action)
    if response:
        return response
    return render(
        request,
        "orders/import_result.html",
        {
            "report": result,
            "title": "Импорт Excel · заказы покупателей",
            "list_href": "/orders/customers",
            "party_label": "Грузополучатель",
            "item_href": "/orders/",
        },
    )


@roles_required("office")
def supplier_orders_import_excel(request):
    upload = request.FILES.get("file")
    filename = getattr(upload, "name", "") or ""
    if not filename:
        return _error(request, "/service/imports", "Выберите файл")
    if not filename.lower().endswith(".xlsx"):
        return _error(request, "/service/imports", "Нужен файл .xlsx")
    payload = upload.read()

    def action():
        return import_supplier_orders(payload, request.user, update=request.POST.get("update") == "1")

    result, response = guarded(request, "/service/imports", action)
    if response:
        return response
    return render(
        request,
        "orders/import_result.html",
        {
            "report": result,
            "title": "Импорт Excel · заказы поставщикам",
            "list_href": "/orders/suppliers",
            "party_label": "Поставщик",
            "item_href": "/orders/suppliers/",
            "revisions_href": "/orders/suppliers/",
        },
    )


@roles_required("warehouse", "receiver")
def supplier_revision_report(request):
    built = request.GET.get("run") == "1"
    rows = []
    if built:
        revisions = list(
            PurchaseOrderRevision.objects.exclude(changes={})
            .select_related("order", "order__supplier", "created_by")
            .order_by("-created_at", "-id")
        )
        for item in revisions:
            changes = item.changes or {}
            if not any(changes.get(key) for key in ("header", "added", "removed", "changed", "blocked")):
                continue
            rows.append({"revision": item, "summary": revision_summary(changes)})
    return render(request, "orders/purchase_revisions.html", {"rows": rows, "built": built})


@roles_required("warehouse", "receiver")
def supplier_order_revisions(request, purchase_id):
    order = get_object_or_404(PurchaseOrder.objects.select_related("supplier"), pk=purchase_id)
    revisions = []
    for item in order.revisions.select_related("created_by").order_by("-revision"):
        revisions.append({"revision": item, "summary": revision_summary(item.changes)})
    return render(request, "orders/purchase_revision_detail.html", {"order": order, "revisions": revisions})


@roles_required("warehouse", "receiver")
def order_revision_report(request):
    built = request.GET.get("run") == "1"
    rows = []
    if built:
        revisions = list(
            OrderRevision.objects.exclude(changes={})
            .select_related("order", "order__consignee", "order__consignee", "created_by")
            .order_by("-created_at", "-id")
        )
        for item in revisions:
            if not item.changes.get("header") and not item.changes.get("added") and not item.changes.get("removed") and not item.changes.get("changed") and not item.changes.get("blocked"):
                continue
            rows.append({"revision": item, "summary": revision_summary(item.changes)})
    return render(request, "orders/revisions.html", {"rows": rows, "built": built})


@roles_required("warehouse", "receiver")
def order_revisions(request, order_id):
    order = get_object_or_404(Order.objects.select_related("consignee"), pk=order_id)
    revisions = []
    for item in order.revisions.select_related("created_by").order_by("-revision"):
        revisions.append({"revision": item, "summary": revision_summary(item.changes)})
    return render(request, "orders/revision_detail.html", {"order": order, "revisions": revisions})


@roles_required("office")
def order_new(request):
    return render(request, "orders/detail.html", _customer_page(None, request.user))


@roles_required("office")
def order_create(request):
    form = request.POST

    def action():
        return save_customer_order(
            None,
            consignee_id=require_int(form, "consignee_id", "Грузополучатель"),
            seller_id=require_int(form, "seller_id", "Продавец"),
            ship_date=parse_date(form.get("ship_date"), "Дата отгрузки"),
            shipping_terms=text(form, "shipping_terms"),
            status=text(form, "status") or "open",
            lines=_customer_lines(form),
            user=request.user,
            number=text(form, "number"),
        )

    result, response = guarded(request, "/orders/new", action)
    if response:
        return response
    return done(request, f"/orders/{result.id}", "Записано")


@roles_required("warehouse", "receiver")
def order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("consignee__category__grade", "consignee__zone").prefetch_related(
            "consignee__allowed_grades",
            "consignee__category__allowed_grades",
            "lines__product",
            "lines__external_product__alias__product",
            "lines__caliber",
            "lines__caliber_from",
            "lines__caliber_to",
            "lines__grades",
            "lines__task_lines",
        ),
        pk=order_id,
    )
    return render(request, "orders/detail.html", _customer_page(order, request.user))


@roles_required("office")
def order_save(request, order_id):
    form = request.POST

    def action():
        order = Order.objects.filter(pk=order_id).first()
        if order is None:
            raise WarehouseError("Заказ не найден")
        closed = order.status == "closed"
        return save_customer_order(
            order,
            consignee_id=order.consignee_id if closed else require_int(form, "consignee_id", "Грузополучатель"),
            seller_id=order.seller_id if closed else require_int(form, "seller_id", "Продавец"),
            ship_date=order.ship_date if closed else parse_date(form.get("ship_date"), "Дата отгрузки"),
            shipping_terms=order.shipping_terms if closed else text(form, "shipping_terms"),
            status=text(form, "status") or order.status,
            lines=[] if closed else _customer_lines(form),
            user=request.user,
            number=order.number if closed else text(form, "number"),
        )

    result, response = guarded(request, f"/orders/{order_id}", action)
    if response:
        return response
    return done(request, f"/orders/{order_id}", "Записано")


def _customer_task_lines(order, form=None):
    lines = []
    posted = bool(form) and any(str(key).startswith("use-") for key in form)
    for line in order.lines.all():
        if not line.for_task:
            continue
        if posted and form.get(f"use-{line.id}") != "1":
            continue
        title = line.external_title() or line.product_title()
        caliber_raw = form.get(f"caliber-{line.id}") if form else ""
        grade_raw = form.get(f"grade-{line.id}") if form else ""
        qty_raw = form.get(f"qty-{line.id}") if form else ""
        if caliber_raw:
            caliber_id = require_int(form, f"caliber-{line.id}", "Калибр")
        elif line.caliber_mode == "exact" and line.caliber_id:
            caliber_id = line.caliber_id
        else:
            caliber_id = None
        from apps.catalog.grade_policy import effective_grade_ids

        effective = effective_grade_ids(line, order.consignee)
        if grade_raw:
            grade_id = require_int(form, f"grade-{line.id}", "Сортность")
        elif effective is not None and len(effective) == 1:
            grade_id = next(iter(effective))
        else:
            grades = list(line.grades.all())
            if len(grades) == 1 and (effective is None or grades[0].id in effective):
                grade_id = grades[0].id
            elif effective is not None and len(effective) == 0:
                raise WarehouseError(f"Грузополучателю {order.consignee.name} нельзя сортность {title}")
            else:
                grade_id = None
        if effective is not None and grade_id is not None and grade_id not in effective:
            raise WarehouseError(f"Грузополучателю {order.consignee.name} нельзя эту сортность для {title}")
        lines.append(
            {
                "order_line_id": line.id,
                "caliber_id": caliber_id,
                "grade_id": grade_id,
                "qty_kg": parse_kg(qty_raw, "Количество") if qty_raw else line.qty_kg,
            }
        )
    return lines


def _load_customer_order(order_id):
    return (
        Order.objects.filter(pk=order_id)
        .select_related("consignee__category", "consignee__zone")
        .prefetch_related(
            "consignee__allowed_grades",
            "consignee__category__allowed_grades",
            "lines__external_product__alias__product",
            "lines__product",
            "lines__grades",
            "lines__caliber",
        )
        .first()
    )


@roles_required("office")
def customer_orders_tasks(request):
    back = back_url(request, "/orders/customers")
    if text(request.POST, "action") == "delete":
        if request.user.role != "admin":
            return _error(request, back, "Недостаточно прав")
        ids = []
        for raw in request.POST.getlist("id"):
            try:
                ids.append(int(raw))
            except ValueError:
                continue
        if not ids:
            return _error(request, back, "Отметьте заказы")
        deleted, failed = delete_customer_orders(ids)
        if deleted:
            notify(request.user, [f"Заказ покупателя {number} — удалён" for number in deleted])
        if not deleted:
            return _error(request, back, "; ".join(failed) or "Заказы не удалены")
        if failed:
            return _error(request, back, "Не удалено: " + "; ".join(failed))
        return redirect(back)
    kind = text(request.POST, "kind") or "pick"
    if kind not in ("pick", "ship"):
        return _error(request, back, "Выберите вид задания")
    raw_ids = request.POST.getlist("id")
    if not raw_ids:
        return _error(request, back, "Отметьте заказы")
    created = []
    failed = []
    for raw in raw_ids:
        try:
            order_id = int(raw)
        except ValueError:
            continue
        order = _load_customer_order(order_id)
        if order is None:
            failed.append(f"{raw}: заказ не найден")
            continue
        try:
            with transaction.atomic():
                task = create_task(
                    order_id=order.id,
                    kind=kind,
                    supplier_id=None,
                    ttn_number="",
                    doc_date=None,
                    lines=_customer_task_lines(order),
                    user=request.user,
                )
            created.append(task)
        except WarehouseError as exc:
            failed.append(f"{order.number}: {exc}")
    if len(created) == 1 and not failed:
        return done(request, f"/tasks/{created[0].id}", "Задание создано")
    if not created:
        return _error(request, back, "; ".join(failed) or "Задания не созданы")
    message = "Создано: " + ", ".join(item.number for item in created)
    if failed:
        message += ". Не создано: " + "; ".join(failed)
    return done(request, back, message)


@roles_required("office")
def supplier_orders_tasks(request):
    back = back_url(request, "/orders/suppliers")
    if text(request.POST, "action") == "delete":
        if request.user.role != "admin":
            return _error(request, back, "Недостаточно прав")
        ids = []
        for raw in request.POST.getlist("id"):
            try:
                ids.append(int(raw))
            except ValueError:
                continue
        if not ids:
            return _error(request, back, "Отметьте заказы")
        deleted, failed = delete_purchase_orders(ids)
        if deleted:
            notify(request.user, [f"Заказ поставщика {number} — удалён" for number in deleted])
        if not deleted:
            return _error(request, back, "; ".join(failed) or "Заказы не удалены")
        if failed:
            return _error(request, back, "Не удалено: " + "; ".join(failed))
        return redirect(back)
    raw_ids = request.POST.getlist("id")
    if not raw_ids:
        return _error(request, back, "Отметьте заказы")
    created = []
    failed = []
    for raw in raw_ids:
        try:
            order_id = int(raw)
        except ValueError:
            continue
        order = PurchaseOrder.objects.filter(pk=order_id).prefetch_related("lines__product").first()
        if order is None:
            failed.append(f"{raw}: заказ не найден")
            continue
        try:
            with transaction.atomic():
                task = create_receive_task(order, request.user)
            created.append(task)
        except WarehouseError as exc:
            failed.append(f"{order.number}: {exc}")
    if len(created) == 1 and not failed:
        return done(request, f"/tasks/{created[0].id}", "Задание записано по заказу")
    if not created:
        return _error(request, back, "; ".join(failed) or "Задания не созданы")
    message = "Создано: " + ", ".join(item.number for item in created)
    if failed:
        message += ". Не создано: " + "; ".join(failed)
    return done(request, back, message)


@roles_required("office")
def order_task(request, order_id):
    form = request.POST

    def action():
        order = _load_customer_order(order_id)
        if order is None:
            raise WarehouseError("Заказ не найден")
        missing = unmapped_titles(order)
        if missing:
            raise WarehouseError(
                "Сначала сопоставьте номенклатуру 1С с номенклатурой: " + ", ".join(missing)
            )
        doc = text(form, "doc_date")
        return create_task(
            order_id=order_id,
            kind=text(form, "kind"),
            supplier_id=optional_int(form, "supplier_id"),
            ttn_number=text(form, "ttn_number"),
            doc_date=parse_date(doc, "Дата прихода") if doc else None,
            lines=_customer_task_lines(order, form),
            user=request.user,
        )

    result, response = guarded(request, f"/orders/{order_id}", action)
    if response:
        return response
    return done(request, f"/tasks/{result.id}", "Задание создано")


def _task_zone_groups(tasks):
    buckets: dict[str, list] = {}
    order: list[str] = []
    for task in tasks:
        zone = ""
        if task.order_id and task.order.consignee_id and task.order.consignee.zone_id:
            zone = task.order.consignee.zone.name
        title = zone or "Без зоны"
        if title not in buckets:
            buckets[title] = []
            order.append(title)
        buckets[title].append(task)
    order.sort(key=lambda name: (name == "Без зоны", name.casefold()))
    return [{"title": title, "tasks": buckets[title]} for title in order]


@roles_required("receiver", "warehouse")
def task_list(request):
    kind = request.GET.get("kind") or ""
    if not kind:
        return render(
            request,
            "hub.html",
            {
                "title": "Задания",
                "items": [
                    {"href": "/tasks?kind=all", "title": "Все"},
                    {"href": "/tasks?kind=receive", "title": "Приёмка"},
                    {"href": "/tasks?kind=pick", "title": "Отбор"},
                    {"href": "/tasks?kind=ship", "title": "На отгрузку"},
                ],
            },
        )
    query = Task.objects.select_related(
        "supplier",
        "assignee",
        "purchase_order__supplier",
        "order__consignee",
        "order__consignee__zone",
    )
    if kind in ("receive", "pick", "ship"):
        query = query.filter(kind=kind)
    else:
        kind = ""
    if not request.user.is_office:
        query = query.filter(Q(assignee_id__in=person_ids(request.user)) | ~Q(kind__in=("receive", "pick", "ship")))
    ship_range = None
    if kind == "pick":
        asked = "ship_from_type" in request.GET
        ship_range = range_from_query(request.GET, "ship", default_from="" if asked else "today")
        if ship_range["from_date"]:
            query = query.filter(order__ship_date__gte=ship_range["from_date"])
        if ship_range["to_date"]:
            query = query.filter(order__ship_date__lte=ship_range["to_date"])
        query = query.annotate(line_count=Count("lines"), weight_kg=Sum("lines__qty_kg"))
    if kind in ("receive", "pick", "ship"):
        status_choices = task_status_choices(kind)
    else:
        status_choices = list(TASK_STATUS.items())
    allowed_status = {key for key, _label in status_choices}
    status = (request.GET.get("status") or "").strip()
    if status == "all":
        pass
    elif status in allowed_status:
        query = query.filter(status=status)
    else:
        status = ""
        query = query.exclude(status__in=("closed", "picked"))
    query = query.order_by("-id")
    tasks = list(query)
    grouped = kind != "receive" and request.GET.get("grouped") == "1"
    groups = _task_zone_groups(tasks) if grouped else []
    staff = {
        item: staff_for_tasks([task for task in tasks if task.kind == item], item)
        for item in ("receive", "pick", "ship")
    }
    if kind in staff:
        assign_staff = staff[kind]
    else:
        assign_staff = []
        known = set()
        for people in staff.values():
            for person in people:
                if person.id in known:
                    continue
                assign_staff.append(person)
                known.add(person.id)
        assign_staff.sort(key=lambda person: (person.name or "").casefold())
    return render(
        request,
        "tasks/list.html",
        {
            "tasks": tasks,
            "kind": kind,
            "grouped": grouped,
            "groups": groups,
            "staff": staff,
            "assign_staff": assign_staff,
            "status": status,
            "status_choices": status_choices,
            "ship_range": ship_range,
        },
    )


def _tasks_back(request) -> str:
    raw_kind = text(request.POST, "kind")
    if raw_kind not in ("receive", "pick", "ship", "all"):
        return "/tasks"
    parts = [f"kind={raw_kind}"]
    for key in ("ship_from_type", "ship_from", "ship_to_type", "ship_to", "grouped", "status"):
        value = (request.POST.get(key) or "").strip()
        if value:
            parts.append(f"{key}={quote(value)}")
    return "/tasks?" + "&".join(parts)


@roles_required("receiver", "warehouse")
def tasks_bulk(request):
    from apps.printing.views import load_print_tasks, task_sheet_response

    raw_kind = text(request.POST, "kind")
    back = _tasks_back(request)
    action = text(request.POST, "action")
    ids = []
    for raw in request.POST.getlist("id"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    if not ids:
        return _error(request, back, "Отметьте задания")
    if action == "print":
        tasks = load_print_tasks(ids)
        if not tasks:
            return _error(request, back, "Задания не найдены")
        return task_sheet_response(tasks, by_groups=text(request.POST, "print_groups") == "1")
    if action == "assign":
        if not request.user.is_office:
            return _error(request, back, "Недостаточно прав")
        user_id = optional_int(request.POST, "user_id")
        if not user_id:
            return _error(request, back, "Выберите сотрудника")
        done_rows = []
        failed = []
        name = ""
        for task_id in ids:
            task = Task.objects.filter(pk=task_id).first()
            if task is None:
                failed.append(f"{task_id}: задание не найдено")
                continue
            try:
                assign_receive_task(task, user_id)
                name = task.assignee.name if task.assignee_id else name
                done_rows.append(task.number or str(task.id))
            except WarehouseError as exc:
                failed.append(f"{task.number or task_id}: {exc}")
        if not done_rows:
            return _error(request, back, "; ".join(failed) or "Ничего не назначено")
        message = f"Назначено сотруднику {name}: " + ", ".join(done_rows)
        if failed:
            message += ". Не назначено: " + "; ".join(failed)
        return done(request, back, message)
    if action in ("reserve", "pick"):
        if raw_kind != "pick":
            return _error(request, back, "Это действие только для отбора")
        runner = reserve_for_task if action == "reserve" else pick_ready_task
        done_rows = []
        failed = []
        for task_id in ids:
            task = (
                Task.objects.select_related("order__consignee")
                .filter(pk=task_id, kind="pick")
                .first()
            )
            if task is None:
                failed.append(f"{task_id}: задание на отбор не найдено")
                continue
            try:
                require_assigned(task, request.user)
                if action == "reserve":
                    detail = runner(task)
                else:
                    detail = runner(task, request.user)
                done_rows.append(f"{task.number}: {detail}")
            except WarehouseError as exc:
                failed.append(f"{task.number}: {exc}")
        if not done_rows:
            return _error(request, back, "; ".join(failed) or "Ничего не выполнено")
        message = ("Спланировано: " if action == "reserve" else "Отобрано: ") + "; ".join(done_rows)
        if failed:
            message += ". Не выполнено: " + "; ".join(failed)
        return done(request, back, message)
    if action != "delete":
        return _error(request, back, "Выберите действие")
    deleted, failed = delete_tasks(ids)
    if deleted:
        notify(request.user, [f"Задание {number} — удалено" for number in deleted])
    if not deleted:
        return _error(request, back, "; ".join(failed) or "Задания не удалены")
    if failed:
        return _error(request, back, "Не удалено: " + "; ".join(failed))
    return redirect(back)


@roles_required("receiver", "warehouse")
def task_detail(request, task_id):
    task = get_object_or_404(
        Task.objects.select_related(
            "order__consignee",
            "order__seller",
            "purchase_order__supplier",
            "supplier",
            "assignee",
        ),
        pk=task_id,
    )
    movements = list(
        Movement.objects.filter(task=task)
        .select_related(
            "pallet__order__consignee",
            "pallet__reserved_consignee",
            "pallet__reserved_line__order__consignee",
            "lot__product",
            "lot__caliber",
            "lot__grade",
        )
        .prefetch_related("pallet__units__packaging")
        .order_by("-id")
    )
    if (
        not request.user.is_office
        and task.kind in ("receive", "pick", "ship")
        and task.assignee_id not in person_ids(request.user)
    ):
        return _error(request, f"/tasks?kind={task.kind}", "Задание вам не назначено")
    context = task_context(task, movements, request.user)
    tab = request.GET.get("tab") or "lines"
    if task.kind != "receive" or tab not in ("lines", "manual"):
        tab = "lines"
    context["tab"] = tab
    return render(request, "tasks/detail.html", context)


def _task_back(request, task_id: int) -> str:
    back = text(request.POST, "next") or f"/tasks/{task_id}"
    if not back.startswith("/") or back.startswith("//"):
        return f"/tasks/{task_id}"
    return back


@roles_required("office")
def task_status(request, task_id):
    back = _task_back(request, task_id)

    def action():
        task = Task.objects.filter(pk=task_id).first()
        if task is None:
            raise WarehouseError("Задание не найдено")
        set_task_status(task, text(request.POST, "status"))
        return task

    result, response = guarded(request, back, action)
    if response:
        return response
    label = dict(task_status_choices(result.kind, result.status)).get(result.status, result.status)
    return done(request, back, f"Статус: {label}")


@roles_required("office")
def task_assign(request, task_id):
    back = _task_back(request, task_id)

    def action():
        task = Task.objects.select_related("assignee").filter(pk=task_id).first()
        if task is None:
            raise WarehouseError("Задание не найдено")
        assign_receive_task(task, optional_int(request.POST, "user_id"))
        return task

    result, response = guarded(request, back, action)
    if response:
        return response
    name = result.assignee.name if result.assignee_id else "не назначен"
    return done(request, back, f"Сотрудник: {name}")


@roles_required("office")
def task_close(request, task_id):
    def action():
        task = Task.objects.filter(pk=task_id).first()
        if task is None:
            raise WarehouseError("Задание не найдено")
        close_task(task)
        return task

    result, response = guarded(request, f"/tasks/{task_id}", action)
    if response:
        return response
    return done(request, f"/tasks/{task_id}", "Задание закрыто")


@roles_required("office")
def task_close_order(request, task_id):
    def action():
        task = Task.objects.select_related("purchase_order", "order").filter(pk=task_id).first()
        if task is None:
            raise WarehouseError("Задание не найдено")
        return close_task_order(task)

    result, response = guarded(request, f"/tasks/{task_id}", action)
    if response:
        return response
    return done(request, f"/tasks/{task_id}", f"Заказ {result.number} закрыт")
