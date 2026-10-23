from datetime import date, datetime, time
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.common.exceptions import WarehouseError
from apps.logistics.models import Driver, Route, RouteOrder
from apps.logistics.queries import delivery_rank, unassigned_orders_queryset
from apps.planning.models import Order, OrderLine, Task


def route_weight(route: Route) -> Decimal:
    total = (
        OrderLine.objects.filter(order__route_orders__route=route)
        .aggregate(total=Sum("qty_kg"))
        .get("total")
    )
    return total or Decimal("0")


def create_route_with_orders(
    *,
    route_date: date,
    driver: Driver | None,
    order_ids: list[int],
    name: str = "",
    status: str = Route.Status.DRAFT,
) -> Route:
    wanted = list(dict.fromkeys(order_ids))
    found = {order.id: order for order in Order.objects.filter(pk__in=wanted, status__in=("open", "partial"))}
    missing = [oid for oid in wanted if oid not in found]
    if missing:
        raise WarehouseError("Некоторые заказы не найдены или закрыты")
    taken = set(
        RouteOrder.objects.filter(
            order_id__in=wanted,
            route__status__in=Route.ASSIGNED_STATUSES,
        ).values_list("order_id", flat=True)
    )
    free = [found[oid] for oid in wanted if oid not in taken]
    if not free:
        raise WarehouseError("Выбранные заказы уже в маршрутах")

    with transaction.atomic():
        route = Route(
            route_date=route_date,
            name=name or (driver.full_name if driver else ""),
            driver=driver,
            status=status,
        )
        route.save()
        RouteOrder.objects.bulk_create(
            [
                RouteOrder(route=route, order=order, position=index)
                for index, order in enumerate(free, start=1)
            ]
        )
    return route


def add_orders_to_route(route: Route, order_ids: list[int]) -> int:
    if route.status == Route.Status.CANCELLED:
        raise WarehouseError("Нельзя менять отменённый маршрут")
    if route.status not in Route.LIVE_STATUSES:
        raise WarehouseError("Нельзя менять маршрут, который уже поставлен к отгрузке или отгружен")
    wanted = list(dict.fromkeys(order_ids))
    found = list(
        Order.objects.filter(pk__in=wanted, status__in=("open", "partial")).select_related(
            "consignee", "consignee__zone"
        )
    )
    taken = set(
        RouteOrder.objects.filter(
            order_id__in=wanted,
            route__status__in=Route.ASSIGNED_STATUSES,
        ).values_list("order_id", flat=True)
    )
    free = [order for order in found if order.id not in taken]
    if not free:
        return 0
    free.sort(key=delivery_rank)
    with transaction.atomic():
        rows = list(
            RouteOrder.objects.filter(route=route)
            .select_related("order__consignee", "order__consignee", "order__consignee__zone")
            .order_by("position", "id")
        )
        sequence = [row.order for row in rows]
        for order in free:
            sequence.insert(_delivery_slot(sequence, order), order)
        by_order = {row.order_id: row for row in rows}
        fresh = []
        for position, order in enumerate(sequence, start=1):
            row = by_order.get(order.id)
            if row is None:
                fresh.append(RouteOrder(route=route, order=order, position=position))
            elif row.position != position:
                row.position = position
                row.save(update_fields=["position"])
        if fresh:
            RouteOrder.objects.bulk_create(fresh)
    return len(free)


def _delivery_slot(sequence: list, order) -> int:
    key = delivery_rank(order)
    last = None
    for index, current in enumerate(sequence):
        if delivery_rank(current) <= key:
            last = index
    return 0 if last is None else last + 1


def remove_order_from_route(route: Route, order_id: int) -> None:
    if route.status not in Route.LIVE_STATUSES:
        raise WarehouseError("Можно менять только черновик или запланированный маршрут")
    deleted, _ = RouteOrder.objects.filter(route=route, order_id=order_id).delete()
    if not deleted:
        raise WarehouseError("Заказ не найден в маршруте")
    _renumber(route)


def delete_routes(ids: list[int]) -> tuple[list[str], list[str]]:
    """Удаляет маршруты; заказы снова свободны для маршрутизации."""
    from django.db.models.deletion import ProtectedError

    deleted = []
    failed = []
    routes = {route.id: route for route in Route.objects.filter(pk__in=ids)}
    for route_id in ids:
        route = routes.get(route_id)
        if route is None:
            failed.append(f"{route_id}: маршрут не найден")
            continue
        label = route.name or f"#{route.id}"
        try:
            with transaction.atomic():
                route.delete()
            deleted.append(label)
        except ProtectedError:
            failed.append(f"{label}: маршрут используется")
    return deleted, failed


def reorder_route_orders(route: Route, order_ids: list[int]) -> None:
    if route.status not in Route.LIVE_STATUSES:
        raise WarehouseError("Можно менять только черновик или запланированный маршрут")
    current = list(RouteOrder.objects.filter(route=route).order_by("position", "id"))
    by_order = {row.order_id: row for row in current}
    if set(order_ids) != set(by_order):
        raise WarehouseError("Список заказов не совпадает с маршрутом")
    with transaction.atomic():
        for index, order_id in enumerate(order_ids, start=1):
            row = by_order[order_id]
            if row.position != index:
                row.position = index
                row.save(update_fields=["position"])


def move_route_order(route: Route, order_id: int, direction: str) -> None:
    if route.status not in Route.LIVE_STATUSES:
        raise WarehouseError("Можно менять только черновик или запланированный маршрут")
    rows = list(RouteOrder.objects.filter(route=route).order_by("position", "id"))
    index = next((i for i, row in enumerate(rows) if row.order_id == order_id), None)
    if index is None:
        raise WarehouseError("Заказ не найден в маршруте")
    if direction == "up" and index > 0:
        rows[index], rows[index - 1] = rows[index - 1], rows[index]
    elif direction == "down" and index < len(rows) - 1:
        rows[index], rows[index + 1] = rows[index + 1], rows[index]
    else:
        return
    with transaction.atomic():
        for pos, row in enumerate(rows, start=1):
            if row.position != pos:
                row.position = pos
                row.save(update_fields=["position"])


def set_route_status(route: Route, status: str) -> Route:
    allowed = {choice.value for choice in Route.Status}
    if status not in allowed:
        raise WarehouseError("Некорректный статус маршрута")
    route.status = status
    route.save()
    return route


def load_local(value: datetime) -> datetime:
    return timezone.localtime(value) if timezone.is_aware(value) else value


def set_planned_load_at(route: Route, value: datetime | None, *, warn_conflict: bool = True) -> str | None:
    warning = None
    if value is not None and route.driver_id:
        local = load_local(value)
        bucket = slot_bucket(value)
        conflict = (
            Route.objects.filter(
                driver_id=route.driver_id,
                planned_load_at__isnull=False,
            )
            .exclude(pk=route.pk)
            .exclude(status=Route.Status.CANCELLED)
        )
        for other in conflict:
            if not other.planned_load_at:
                continue
            other_local = load_local(other.planned_load_at)
            if other_local.date() == local.date() and slot_bucket(other.planned_load_at) == bucket:
                warning = f"У водителя уже есть маршрут в слоте {local.strftime('%d.%m')} {bucket}"
                break
    route.planned_load_at = value
    route.save(update_fields=["planned_load_at", "updated_at"])
    return warning if warn_conflict else None


def slot_bucket(value: datetime) -> str:
    local = load_local(value)
    minute = 0 if local.minute < 30 else 30
    return f"{local.hour:02d}:{minute:02d}"


def parse_load_datetime(load_date: date | None, raw_time: str) -> datetime | None:
    """Дата и время погрузки. Дата может отличаться от даты рейса (погрузка накануне)."""
    text = (raw_time or "").strip()
    if not text:
        return None
    if load_date is None:
        raise WarehouseError("Укажите дату погрузки")
    try:
        parts = text.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        naive = datetime.combine(load_date, time(hour=hour, minute=minute))
    except (TypeError, ValueError, IndexError) as exc:
        raise WarehouseError("Некорректное время погрузки") from exc
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def parse_load_date(raw: str) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WarehouseError("Некорректная дата погрузки") from exc


def update_route_header(
    route: Route,
    *,
    name: str,
    driver: Driver | None,
    notes: str,
    status: str,
    planned_load_at: datetime | None,
    actual_load_at: datetime | None = None,
) -> str | None:
    allowed = {choice.value for choice in Route.Status}
    if status not in allowed:
        raise WarehouseError("Некорректный статус маршрута")
    route.name = name
    route.driver = driver
    route.notes = notes
    route.status = status
    warning = None
    route.planned_load_at = planned_load_at
    route.actual_load_at = actual_load_at
    if status in Route.LIVE_STATUSES:
        route.docked_at = None
    elif status == Route.Status.DOCKED and route.docked_at is None:
        route.docked_at = timezone.now()
    elif status == Route.Status.COMPLETED and route.actual_load_at is None:
        route.actual_load_at = timezone.now()
    if planned_load_at and route.driver_id:
        local = load_local(planned_load_at)
        bucket = slot_bucket(planned_load_at)
        for other in Route.objects.filter(
            driver_id=route.driver_id,
            planned_load_at__isnull=False,
        ).exclude(pk=route.pk).exclude(status=Route.Status.CANCELLED):
            if not other.planned_load_at:
                continue
            other_local = load_local(other.planned_load_at)
            if other_local.date() == local.date() and slot_bucket(other.planned_load_at) == bucket:
                warning = f"У водителя уже есть маршрут в слоте {local.strftime('%d.%m')} {bucket}"
                break
    route.save()
    return warning


def ship_tasks_for_orders(order_ids: list[int]) -> dict[int, list[Task]]:
    if not order_ids:
        return {}
    tasks = Task.objects.filter(order_id__in=order_ids, kind="ship").exclude(status="cancelled").order_by("-id")
    result: dict[int, list[Task]] = {}
    for task in tasks:
        result.setdefault(task.order_id, []).append(task)
    return result


def _renumber(route: Route) -> None:
    rows = list(RouteOrder.objects.filter(route=route).order_by("position", "id"))
    for index, row in enumerate(rows, start=1):
        if row.position != index:
            row.position = index
            row.save(update_fields=["position"])


def available_orders_for_route(route: Route):
    return unassigned_orders_queryset(ship_date=route.route_date)
