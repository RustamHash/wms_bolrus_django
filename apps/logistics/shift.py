from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Prefetch, Sum
from django.utils import timezone

from apps.common.constants import TASK_STATUS
from apps.common.exceptions import WarehouseError
from apps.logistics.models import Route, RouteOrder
from apps.logistics.queries import order_weights
from apps.logistics.schedule import _sort_key_plan
from apps.planning.models import Order, PurchaseOrder, Task


def _stamp(obj, field: str, label: str) -> None:
    if getattr(obj, field) is not None:
        raise WarehouseError(f"{label} уже зафиксировано")
    setattr(obj, field, timezone.now())
    obj.save(update_fields=[field])


def parse_shift_date(raw: str) -> date:
    value = (raw or "").strip()
    if not value:
        return timezone.localdate()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WarehouseError("Некорректная дата") from exc


def parse_shift_period(raw_from: str, raw_to: str, raw_date: str = "") -> tuple[date, date, str]:
    today = timezone.localdate()
    start_raw = (raw_from or "").strip()
    end_raw = (raw_to or "").strip()
    single = (raw_date or "").strip()
    if not start_raw and not end_raw and single:
        start_raw = end_raw = single
    if not start_raw and not end_raw:
        return today, today, "today"
    start = parse_shift_date(start_raw or end_raw)
    end = parse_shift_date(end_raw or start_raw)
    if end < start:
        raise WarehouseError("Дата окончания раньше начала")
    preset = ""
    if start == end:
        if start == today:
            preset = "today"
        elif start == today - timedelta(days=1):
            preset = "yesterday"
        elif start == today + timedelta(days=1):
            preset = "tomorrow"
    return start, end, preset


def task_status_label(tasks, work: str) -> str:
    if not tasks:
        return "Нет задания"
    statuses = {task.status for task in tasks}
    if work in statuses:
        return TASK_STATUS[work]
    if "open" in statuses:
        return TASK_STATUS["open"]
    if statuses <= {"closed", "picked"}:
        if statuses == {"picked"}:
            return TASK_STATUS["picked"]
        return TASK_STATUS["closed"]
    status = next(iter(statuses))
    return TASK_STATUS.get(status, status)


def shift_orders(start: date, end: date) -> list[dict]:
    orders = list(
        Order.objects.filter(ship_date__gte=start, ship_date__lte=end)
        .select_related("consignee", "consignee__zone")
        .prefetch_related(
            Prefetch(
                "route_orders",
                queryset=RouteOrder.objects.filter(route__status__in=Route.ASSIGNED_STATUSES).select_related("route"),
            ),
            Prefetch("tasks", queryset=Task.objects.filter(kind="pick")),
        )
        .order_by("ship_date", "id")
    )
    weights = order_weights(orders)
    rows = []
    for order in orders:
        names = [link.route.name or f"Маршрут {link.route_id}" for link in order.route_orders.all()]
        rows.append(
            {
                "id": order.id,
                "date": order.ship_date,
                "number": order.number or order.external_number or f"#{order.pk}",
                "consignee": order.consignee.name if order.consignee_id else "",
                "zone": order.consignee.zone.name if order.consignee_id and order.consignee.zone_id else "",
                "weight": weights.get(order.id, Decimal("0")),
                "plans": [link.route.planned_load_at for link in order.route_orders.all() if link.route.planned_load_at],
                "route": ", ".join(names),
                "pick": task_status_label(list(order.tasks.all()), "created"),
            }
        )
    return rows


def shift_incoming(start: date, end: date) -> list[dict]:
    orders = list(
        PurchaseOrder.objects.filter(receipt_date__gte=start, receipt_date__lte=end)
        .select_related("supplier")
        .prefetch_related(Prefetch("tasks", queryset=Task.objects.filter(kind="receive")))
        .annotate(weight=Sum("lines__qty_kg"))
        .order_by("receipt_date", "id")
    )
    rows = []
    for order in orders:
        rows.append(
            {
                "id": order.id,
                "date": order.receipt_date,
                "number": order.number or order.external_number or f"#{order.pk}",
                "supplier": order.supplier.name,
                "weight": order.weight or Decimal("0"),
                "status": order.status,
                "receive": task_status_label(list(order.tasks.all()), "to_receive"),
                "comment": order.comment,
                "arrived_at": order.arrived_at,
                "gate_at": order.gate_at,
                "receive_finished_at": order.receive_finished_at,
                "departed_at": order.departed_at,
            }
        )
    return rows


def shift_routes(start: date, end: date) -> list[Route]:
    routes = list(
        Route.objects.filter(route_date__gte=start, route_date__lte=end)
        .exclude(status=Route.Status.CANCELLED)
        .select_related("driver")
        .annotate(
            order_count=Count("route_orders", distinct=True),
            weight=Sum("route_orders__order__lines__qty_kg"),
        )
    )
    routes.sort(key=lambda route: (_sort_key_plan(route.planned_load_at), route.id))
    return routes


def dock_route(route: Route) -> Route:
    if route.status not in Route.LIVE_STATUSES:
        raise WarehouseError("Поставить к отгрузке можно черновик или запланированный маршрут")
    route.status = Route.Status.DOCKED
    if route.docked_at is None:
        route.docked_at = timezone.now()
    route.save()
    return route


def ship_route(route: Route) -> Route:
    if route.status != Route.Status.DOCKED:
        raise WarehouseError("Отгрузить можно только маршрут, поставленный к отгрузке")
    route.status = Route.Status.COMPLETED
    if route.actual_load_at is None:
        route.actual_load_at = timezone.now()
    route.save()
    return route


def arrive_route(route: Route) -> Route:
    _stamp(route, "arrived_at", "Прибытие")
    return route


def depart_route(route: Route) -> Route:
    _stamp(route, "departed_at", "Убытие")
    return route


def set_incoming_comment(order: PurchaseOrder, comment: str) -> PurchaseOrder:
    order.comment = comment
    order.save(update_fields=["comment"])
    return order


def arrive_incoming(order: PurchaseOrder) -> PurchaseOrder:
    _stamp(order, "arrived_at", "Прибытие")
    return order


def gate_incoming(order: PurchaseOrder) -> PurchaseOrder:
    _stamp(order, "gate_at", "Постановка на ворота")
    return order


def finish_incoming(order: PurchaseOrder) -> PurchaseOrder:
    _stamp(order, "receive_finished_at", "Окончание приёмки")
    return order


def depart_incoming(order: PurchaseOrder) -> PurchaseOrder:
    _stamp(order, "departed_at", "Убытие")
    return order


def revert_route(route: Route) -> Route:
    if route.status == Route.Status.DOCKED:
        route.status = Route.Status.PLANNED
        route.docked_at = None
        route.save()
        return route
    if route.status == Route.Status.COMPLETED:
        route.status = Route.Status.DOCKED
        route.actual_load_at = None
        route.save()
        return route
    raise WarehouseError("Этот маршрут нельзя вернуть")
