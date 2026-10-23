import json
from datetime import date, timedelta

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, text
from apps.common.http import back_url, done, guarded, roles_required
from apps.logistics.dirs import apply_driver, apply_vehicle
from apps.logistics.models import Driver, Route, RouteOrder, Vehicle
from apps.planning.models import PurchaseOrder
from apps.logistics.queries import unassigned_orders_queryset
from apps.logistics.routes import (
    add_orders_to_route,
    available_orders_for_route,
    delete_routes,
    load_local,
    parse_load_date,
    parse_load_datetime,
    remove_order_from_route,
    reorder_route_orders,
    route_weight,
    set_planned_load_at,
    ship_tasks_for_orders,
    update_route_header,
)
from apps.logistics.routing import (
    add_assigned_orders_to_route,
    clear_session_assignments,
    clear_session_order_assignments,
    consumed_for_create,
    consumed_for_route,
    create_routes_from_zone_assignments,
    driver_load_summary,
    routing_rows,
    set_session_assignment,
    set_session_order_assignment,
)
from apps.logistics.schedule import schedule_context
from apps.logistics.shift import (
    arrive_incoming,
    arrive_route,
    depart_incoming,
    depart_route,
    dock_route,
    finish_incoming,
    gate_incoming,
    parse_shift_period,
    revert_route,
    set_incoming_comment,
    shift_incoming,
    shift_orders,
    shift_routes,
    ship_route,
)


def _today() -> date:
    return timezone.localdate()


def _ship_date(request) -> date:
    raw = (request.GET.get("date") or request.POST.get("date") or "").strip()
    if not raw:
        return _today() + timedelta(days=1)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise WarehouseError("Некорректная дата") from exc


def _posted_driver_id(request) -> int | None:
    raw_driver = text(request.POST, "driver_id")
    if raw_driver == "":
        return None
    try:
        driver_id = int(raw_driver)
    except ValueError as exc:
        raise WarehouseError("Некорректный водитель") from exc
    if driver_id < 0:
        raise WarehouseError("Некорректный водитель")
    return driver_id


@roles_required("office")
def index(request):
    day = _today() + timedelta(days=1)
    return render(
        request,
        "logistics/index.html",
        {
            "title": "Логистика",
            "day": day,
            "route_count": Route.objects.filter(route_date=day).exclude(status=Route.Status.CANCELLED).count(),
            "unassigned": unassigned_orders_queryset(day).count(),
            "drivers": Driver.objects.filter(is_active=True).count(),
        },
    )


# --- directories ---


@roles_required("office")
def vehicle_list(request):
    return render(
        request,
        "logistics/vehicle_list.html",
        {"title": "Автомобили", "items": Vehicle.objects.order_by("-is_active", "plate_number")},
    )


@roles_required("office")
def vehicle_new(request):
    return render(request, "logistics/vehicle_form.html", {"title": "Автомобиль", "item": None})


@roles_required("office")
def vehicle_create(request):
    def action():
        return apply_vehicle(Vehicle(), request.POST)

    _, response = guarded(request, "/logistics/vehicles/new", action)
    if response:
        return response
    return done(request, "/logistics/vehicles", "Сохранено")


@roles_required("office")
def vehicle_edit(request, item_id):
    item = get_object_or_404(Vehicle, pk=item_id)
    return render(request, "logistics/vehicle_form.html", {"title": "Автомобиль", "item": item})


@roles_required("office")
def vehicle_update(request, item_id):
    def action():
        item = Vehicle.objects.filter(pk=item_id).first()
        if item is None:
            raise WarehouseError("Автомобиль не найден")
        return apply_vehicle(item, request.POST)

    _, response = guarded(request, f"/logistics/vehicles/{item_id}", action)
    if response:
        return response
    return done(request, "/logistics/vehicles", "Сохранено")


@roles_required("office")
def driver_list(request):
    return render(
        request,
        "logistics/driver_list.html",
        {
            "title": "Водители",
            "items": Driver.objects.select_related("vehicle").order_by("-is_active", "full_name"),
        },
    )


@roles_required("office")
def driver_new(request):
    return render(
        request,
        "logistics/driver_form.html",
        {
            "title": "Водитель",
            "item": None,
            "vehicles": Vehicle.objects.filter(is_active=True).order_by("plate_number"),
        },
    )


@roles_required("office")
def driver_create(request):
    def action():
        return apply_driver(Driver(), request.POST)

    _, response = guarded(request, "/logistics/drivers/new", action)
    if response:
        return response
    return done(request, "/logistics/drivers", "Сохранено")


@roles_required("office")
def driver_edit(request, item_id):
    item = get_object_or_404(Driver.objects.select_related("vehicle"), pk=item_id)
    return render(
        request,
        "logistics/driver_form.html",
        {
            "title": "Водитель",
            "item": item,
            "vehicles": Vehicle.objects.filter(is_active=True).order_by("plate_number"),
        },
    )


@roles_required("office")
def driver_update(request, item_id):
    def action():
        item = Driver.objects.filter(pk=item_id).first()
        if item is None:
            raise WarehouseError("Водитель не найден")
        return apply_driver(item, request.POST)

    _, response = guarded(request, f"/logistics/drivers/{item_id}", action)
    if response:
        return response
    return done(request, "/logistics/drivers", "Сохранено")


# --- Routing ---


@roles_required("office")
def routing(request):
    try:
        ship_date = _ship_date(request)
    except WarehouseError as exc:
        messages.error(request, str(exc))
        ship_date = _today() + timedelta(days=1)
    rows, assignments, total_weight = routing_rows(ship_date, request.session)
    drivers = list(Driver.objects.filter(is_active=True).select_related("vehicle").order_by("full_name"))
    existing = list(
        Route.objects.filter(route_date=ship_date, status__in=Route.LIVE_STATUSES)
        .select_related("driver")
        .order_by("name", "id")
    )
    return render(
        request,
        "logistics/routing.html",
        {
            "title": "Маршрутизация",
            "ship_date": ship_date,
            "rows": rows,
            "assignments": assignments,
            "drivers": drivers,
            "driver_loads": driver_load_summary(rows, drivers),
            "existing_routes": existing,
            "total_weight": total_weight,
            "total_orders": sum(row["order_count"] for row in rows),
        },
    )


@roles_required("office")
def routing_assign(request):
    def action():
        ship_date = _ship_date(request)
        zone_id = optional_int(request.POST, "zone_id")
        if zone_id is None:
            raise WarehouseError("Выберите зону")
        set_session_assignment(request.session, ship_date, zone_id, _posted_driver_id(request))
        return ship_date

    result, response = guarded(request, "/logistics/routing", action)
    if response:
        return response
    return redirect(f"/logistics/routing?date={result.isoformat()}")


@roles_required("office")
def routing_assign_order(request):
    def action():
        ship_date = _ship_date(request)
        order_id = optional_int(request.POST, "order_id")
        if order_id is None:
            raise WarehouseError("Выберите заказ")
        if not unassigned_orders_queryset(ship_date).filter(pk=order_id).exists():
            raise WarehouseError("Заказ не найден среди свободных")
        set_session_order_assignment(request.session, ship_date, order_id, _posted_driver_id(request))
        return ship_date

    result, response = guarded(request, "/logistics/routing", action)
    if response:
        return response
    return redirect(f"/logistics/routing?date={result.isoformat()}")


def _posted_load_times(request, ship_date):
    """Время загрузки по водителю. Пустое поле — маршрут без плана."""
    load_day = parse_load_date(text(request.POST, "planned_load_date")) or ship_date
    times = {}
    for key, raw in request.POST.items():
        if not key.startswith("load_time_"):
            continue
        value = (raw or "").strip()
        if not value:
            continue
        try:
            driver_id = int(key.removeprefix("load_time_"))
        except ValueError as exc:
            raise WarehouseError("Некорректное время загрузки") from exc
        times[driver_id] = parse_load_datetime(load_day, value)
    return times


@roles_required("office")
def routing_create(request):
    def action():
        ship_date = _ship_date(request)
        load_times = _posted_load_times(request, ship_date)
        rows, assignments, _total = routing_rows(ship_date, request.session)
        created = create_routes_from_zone_assignments(ship_date=ship_date, zone_rows=rows)
        warnings = []
        for route in created:
            load_at = load_times.get(route.driver_id or 0)
            if load_at is None:
                continue
            warning = set_planned_load_at(route, load_at)
            if warning:
                warnings.append(warning)
        order_ids, zone_ids = consumed_for_create(rows)
        present = {row["zone_id"] for row in rows}
        stale_zones = [zone_id for zone_id in assignments if zone_id not in present]
        clear_session_assignments(request.session, ship_date, list(zone_ids) + stale_zones)
        clear_session_order_assignments(request.session, ship_date, order_ids)
        return ship_date, created, warnings

    result, response = guarded(request, "/logistics/routing", action)
    if response:
        return response
    ship_date, created, warnings = result
    messages.success(request, f"Создано маршрутов: {len(created)}")
    for warning in warnings:
        messages.warning(request, warning)
    return redirect(f"/logistics/routing?date={ship_date.isoformat()}")


@roles_required("office")
def routing_add_to_route(request):
    def action():
        ship_date = _ship_date(request)
        route_id = optional_int(request.POST, "route_id")
        route = Route.objects.filter(pk=route_id).first()
        if route is None:
            raise WarehouseError("Маршрут не найден")
        rows, _assignments, _total = routing_rows(ship_date, request.session)
        raw_time = text(request.POST, "planned_load_time")
        load_at = None
        if raw_time:
            load_day = parse_load_date(text(request.POST, "planned_load_date")) or ship_date
            load_at = parse_load_datetime(load_day, raw_time)
        added = add_assigned_orders_to_route(route=route, zone_rows=rows)
        warning = set_planned_load_at(route, load_at) if load_at is not None else None
        if added:
            order_ids, zone_ids = consumed_for_route(route, rows)
            clear_session_assignments(request.session, ship_date, zone_ids)
            clear_session_order_assignments(request.session, ship_date, order_ids)
        return ship_date, added, warning

    result, response = guarded(request, "/logistics/routing", action)
    if response:
        return response
    ship_date, added, warning = result
    messages.success(request, f"Добавлено заказов: {added}")
    if warning:
        messages.warning(request, warning)
    return redirect(f"/logistics/routing?date={ship_date.isoformat()}")


# --- Routes ---


@roles_required("office")
def route_list(request):
    try:
        day = _ship_date(request)
    except WarehouseError as exc:
        messages.error(request, str(exc))
        day = _today() + timedelta(days=1)
    routes = list(
        Route.objects.filter(route_date=day)
        .select_related("driver")
        .prefetch_related("route_orders")
        .order_by("status", "name", "id")
    )
    for route in routes:
        route.weight_kg = route_weight(route)
        route.stops = route.route_orders.count()
    return render(
        request,
        "logistics/route_list.html",
        {"title": "Маршруты", "day": day, "routes": routes, "statuses": Route.Status},
    )


@roles_required("office")
def routes_bulk(request):
    from apps.printing.views import load_print_routes, route_sheet_response

    day = (request.POST.get("date") or "").strip()
    back = f"/logistics/routes?date={day}" if day else back_url(request, "/logistics/routes")
    action = text(request.POST, "action")
    ids = []
    for raw in request.POST.getlist("id"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    if not ids:
        messages.error(request, "Отметьте маршруты")
        return redirect(back)
    if action == "print":
        sheets = load_print_routes(ids)
        if not sheets:
            messages.error(request, "Маршруты не найдены")
            return redirect(back)
        return route_sheet_response(sheets)
    if action != "delete":
        messages.error(request, "Выберите действие")
        return redirect(back)
    deleted, failed = delete_routes(ids)
    if deleted:
        messages.success(request, f"Удалено: {', '.join(deleted)}")
    if failed:
        messages.error(request, "Не удалено: " + "; ".join(failed))
    if not deleted and not failed:
        messages.error(request, "Маршруты не удалены")
    return redirect(back)


@roles_required("office")
def route_delete(request, route_id):
    route = get_object_or_404(Route, pk=route_id)
    day = route.route_date.isoformat()
    back_list = f"/logistics/routes?date={day}"

    def action():
        deleted, failed = delete_routes([route.id])
        if not deleted:
            raise WarehouseError("; ".join(failed) or "Маршрут не удалён")
        return deleted[0]

    result, response = guarded(request, f"/logistics/routes/{route_id}", action)
    if response:
        return response
    return done(request, back_list, f"Маршрут «{result}» удалён")


@roles_required("office")
def route_detail(request, route_id):
    route = get_object_or_404(
        Route.objects.select_related("driver", "driver__vehicle"),
        pk=route_id,
    )
    stops = list(
        RouteOrder.objects.filter(route=route)
        .select_related("order__consignee", "order__consignee", "order__consignee__zone")
        .order_by("position", "id")
    )
    order_ids = [stop.order_id for stop in stops]
    tasks_map = ship_tasks_for_orders(order_ids)
    weights = {}
    from apps.logistics.queries import order_weights

    weights = order_weights([stop.order for stop in stops])
    for stop in stops:
        stop.weight_kg = weights.get(stop.order_id)
        stop.ship_tasks = tasks_map.get(stop.order_id, [])
    available = list(available_orders_for_route(route)[:50])
    load_date = route.route_date.isoformat()
    load_time = ""
    if route.planned_load_at:
        local = load_local(route.planned_load_at)
        load_date = local.date().isoformat()
        load_time = local.strftime("%H:%M")
    actual_date = load_date
    actual_time = ""
    if route.actual_load_at:
        actual_local = load_local(route.actual_load_at)
        actual_date = actual_local.date().isoformat()
        actual_time = actual_local.strftime("%H:%M")
    return render(
        request,
        "logistics/route_detail.html",
        {
            "title": str(route),
            "route": route,
            "stops": stops,
            "drivers": Driver.objects.filter(is_active=True).order_by("full_name"),
            "available": available,
            "load_date": load_date,
            "load_time": load_time,
            "actual_date": actual_date,
            "actual_time": actual_time,
            "total_kg": route_weight(route),
            "statuses": Route.Status,
        },
    )


@roles_required("office")
def route_save(request, route_id):
    def action():
        route = Route.objects.filter(pk=route_id).first()
        if route is None:
            raise WarehouseError("Маршрут не найден")
        driver_id = optional_int(request.POST, "driver_id")
        driver = None
        if driver_id:
            driver = Driver.objects.filter(pk=driver_id, is_active=True).first()
            if driver is None:
                raise WarehouseError("Водитель не найден")
        status = text(request.POST, "status") or route.status
        raw_time = text(request.POST, "planned_load_time")
        load_day = parse_load_date(text(request.POST, "planned_load_date")) or route.route_date
        load_at = parse_load_datetime(load_day, raw_time) if raw_time else None
        raw_actual = text(request.POST, "actual_load_time")
        actual_day = parse_load_date(text(request.POST, "actual_load_date")) or load_day
        actual_at = parse_load_datetime(actual_day, raw_actual) if raw_actual else None
        warning = update_route_header(
            route,
            name=text(request.POST, "name"),
            driver=driver,
            notes=text(request.POST, "notes"),
            status=status,
            planned_load_at=load_at,
            actual_load_at=actual_at,
        )
        return route, warning

    result, response = guarded(request, f"/logistics/routes/{route_id}", action)
    if response:
        return response
    route, warning = result
    if warning:
        messages.warning(request, warning)
    return done(request, f"/logistics/routes/{route.id}", "Сохранено")


@roles_required("office")
def route_add_orders(request, route_id):
    def action():
        route = Route.objects.filter(pk=route_id).first()
        if route is None:
            raise WarehouseError("Маршрут не найден")
        order_ids = []
        for raw in request.POST.getlist("order_id"):
            try:
                order_ids.append(int(raw))
            except (TypeError, ValueError) as exc:
                raise WarehouseError("Некорректный заказ") from exc
        added = add_orders_to_route(route, order_ids)
        return route, added

    result, response = guarded(request, f"/logistics/routes/{route_id}", action)
    if response:
        return response
    route, added = result
    messages.success(request, f"Добавлено заказов: {added}")
    return redirect(f"/logistics/routes/{route.id}")


@roles_required("office")
def route_remove_order(request, route_id, order_id):
    def action():
        route = Route.objects.filter(pk=route_id).first()
        if route is None:
            raise WarehouseError("Маршрут не найден")
        remove_order_from_route(route, order_id)
        return route

    result, response = guarded(request, f"/logistics/routes/{route_id}", action)
    if response:
        return response
    return done(request, f"/logistics/routes/{result.id}", "Заказ убран")


@roles_required("office")
def route_move_order(request, route_id, order_id):
    def action():
        from apps.logistics.routes import move_route_order

        route = Route.objects.filter(pk=route_id).first()
        if route is None:
            raise WarehouseError("Маршрут не найден")
        direction = text(request.POST, "direction")
        if direction not in ("up", "down"):
            raise WarehouseError("Некорректное направление")
        move_route_order(route, order_id, direction)
        return route

    result, response = guarded(request, f"/logistics/routes/{route_id}", action)
    if response:
        return response
    return redirect(f"/logistics/routes/{result.id}")


@roles_required("office")
def route_reorder(request, route_id):
    route = Route.objects.filter(pk=route_id).first()
    if route is None:
        return JsonResponse({"ok": False, "error": "Маршрут не найден"}, status=404)
    try:
        payload = json.loads(request.body.decode() or "{}")
        order_ids = [int(item) for item in payload.get("order_ids") or []]
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Некорректный порядок"}, status=400)
    try:
        reorder_route_orders(route, order_ids)
    except WarehouseError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True})


# --- Schedule ---


@roles_required("office")
def schedule(request):
    try:
        day = _ship_date(request)
    except WarehouseError as exc:
        messages.error(request, str(exc))
        day = _today() + timedelta(days=1)
    ctx = schedule_context(day)
    ctx["title"] = "График отгрузки"
    return render(request, "logistics/schedule.html", ctx)


@roles_required("office")
def schedule_set_time(request):
    day_raw = (request.POST.get("date") or "").strip()

    def action():
        route_id = optional_int(request.POST, "route_id")
        route = Route.objects.filter(pk=route_id).first()
        if route is None:
            raise WarehouseError("Маршрут не найден")
        raw = text(request.POST, "planned_load_time")
        load_day = parse_load_date(text(request.POST, "planned_load_date") or day_raw) or route.route_date
        load_at = parse_load_datetime(load_day, raw) if raw else None
        warning = set_planned_load_at(route, load_at)
        return route, load_day, warning

    back = f"/logistics/schedule?date={day_raw}" if day_raw else "/logistics/schedule"
    result, response = guarded(request, back, action)
    if response:
        return response
    route, load_day, warning = result
    if warning:
        messages.warning(request, warning + ". Время всё равно сохранено — в слоте могут быть несколько маршрутов.")
    else:
        messages.success(request, "Время погрузки сохранено")
    schedule_day = day_raw or route.route_date.isoformat()
    return redirect(f"/logistics/schedule?date={schedule_day}")


# --- Shift ---


def _shift_back(request) -> str:
    start = (request.POST.get("date_from") or request.POST.get("date") or "").strip()
    end = (request.POST.get("date_to") or start).strip()
    if start and end:
        return f"/shift?date_from={start}&date_to={end}"
    return "/shift"


_SHIFT_PARTS = {
    "orders": "logistics/_shift_orders.html",
    "incoming": "logistics/_shift_incoming.html",
    "routes": "logistics/_shift_routes.html",
}


def _shift_context(start, end, preset, part=""):
    today = _today()
    context = {
        "title": "Смена",
        "date_from": start,
        "date_to": end,
        "preset": preset,
        "ranged": start != end,
        "yesterday": today - timedelta(days=1),
        "today": today,
        "tomorrow": today + timedelta(days=1),
    }
    if part in ("", "orders"):
        context["orders"] = shift_orders(start, end)
    if part in ("", "incoming"):
        context["incoming"] = shift_incoming(start, end)
    if part in ("", "routes"):
        context["routes"] = shift_routes(start, end)
    return context


def _shift_bounds(request):
    today = _today()
    try:
        return parse_shift_period(
            request.GET.get("date_from") or "",
            request.GET.get("date_to") or "",
            request.GET.get("date") or "",
        )
    except WarehouseError as exc:
        messages.error(request, str(exc))
        return today, today, "today"


@roles_required("shift")
def shift_board(request):
    start, end, preset = _shift_bounds(request)
    part = request.GET.get("part") or ""
    if part not in _SHIFT_PARTS:
        part = ""
    context = _shift_context(start, end, preset, part)
    if part:
        return render(request, _SHIFT_PARTS[part], context)
    return render(request, "logistics/shift.html", context)


def _print_task_ids(order_ids, *, field: str, kind: str) -> list[int]:
    from apps.planning.models import Task

    if not order_ids:
        return []
    attr = f"{field}_id"
    grouped: dict[int, list[int]] = {}
    query = Task.objects.filter(**{f"{field}_id__in": order_ids, "kind": kind}).order_by("id")
    for task in query:
        grouped.setdefault(getattr(task, attr), []).append(task.id)
    return [task_id for order_id in order_ids for task_id in grouped.get(order_id, [])]


def _shift_tasks_pdf(request, *, source: str, kind: str, empty: str):
    from apps.printing.views import load_print_tasks, task_sheet_response

    start, end, _preset = _shift_bounds(request)
    if source == "orders":
        order_ids = [row["id"] for row in shift_orders(start, end)]
        field = "order"
    else:
        order_ids = [row["id"] for row in shift_incoming(start, end)]
        field = "purchase_order"
    tasks = load_print_tasks(_print_task_ids(order_ids, field=field, kind=kind))
    if not tasks:
        return HttpResponse(empty)
    return task_sheet_response(tasks)


@roles_required("shift")
def shift_orders_print(request):
    return _shift_tasks_pdf(request, source="orders", kind="pick", empty="Нет заданий на отбор за выбранный период")


@roles_required("shift")
def shift_incoming_print(request):
    return _shift_tasks_pdf(request, source="incoming", kind="receive", empty="Нет заданий на приёмку за выбранный период")


@roles_required("shift")
def shift_routes_print(request):
    from apps.printing.views import load_print_routes, route_sheet_response

    start, end, _preset = _shift_bounds(request)
    routes = shift_routes(start, end)
    sheets = load_print_routes([route.id for route in routes])
    if not sheets:
        return HttpResponse("Нет маршрутов за выбранный период")
    return route_sheet_response(sheets)


def _shift_action(request, route_id, fn, message):
    def action():
        route = Route.objects.filter(pk=route_id).first()
        if route is None:
            raise WarehouseError("Маршрут не найден")
        fn(route)
        return route

    result, response = guarded(request, _shift_back(request), action)
    if response:
        return response
    return done(request, _shift_back(request), message)


@roles_required("shift")
def shift_dock(request, route_id):
    return _shift_action(request, route_id, dock_route, "Маршрут поставлен к отгрузке")


@roles_required("shift")
def shift_ship(request, route_id):
    return _shift_action(request, route_id, ship_route, "Маршрут отгружен")


@roles_required("shift")
def shift_revert(request, route_id):
    return _shift_action(request, route_id, revert_route, "Статус маршрута возвращён")


@roles_required("shift")
def shift_arrive(request, route_id):
    return _shift_action(request, route_id, arrive_route, "Прибытие зафиксировано")


@roles_required("shift")
def shift_depart(request, route_id):
    return _shift_action(request, route_id, depart_route, "Убытие зафиксировано")


def _incoming_action(request, order_id, fn, message):
    def action():
        order = PurchaseOrder.objects.filter(pk=order_id).first()
        if order is None:
            raise WarehouseError("Входящий заказ не найден")
        fn(order)
        return order

    result, response = guarded(request, _shift_back(request), action)
    if response:
        return response
    return done(request, _shift_back(request), message)


@roles_required("shift")
def shift_incoming_comment(request, order_id):
    def action():
        order = PurchaseOrder.objects.filter(pk=order_id).first()
        if order is None:
            raise WarehouseError("Входящий заказ не найден")
        return set_incoming_comment(order, text(request.POST, "comment"))

    result, response = guarded(request, _shift_back(request), action)
    if response:
        return response
    return done(request, _shift_back(request), "Комментарий сохранён")


@roles_required("shift")
def shift_incoming_arrive(request, order_id):
    return _incoming_action(request, order_id, arrive_incoming, "Прибытие зафиксировано")


@roles_required("shift")
def shift_incoming_gate(request, order_id):
    return _incoming_action(request, order_id, gate_incoming, "Постановка на ворота зафиксирована")


@roles_required("shift")
def shift_incoming_finish(request, order_id):
    return _incoming_action(request, order_id, finish_incoming, "Окончание приёмки зафиксировано")


@roles_required("shift")
def shift_incoming_depart(request, order_id):
    return _incoming_action(request, order_id, depart_incoming, "Убытие зафиксировано")
