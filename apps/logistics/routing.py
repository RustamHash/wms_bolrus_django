"""Маршрутизация: зона → водитель → создание маршрутов."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Iterable

from apps.common.exceptions import WarehouseError
from apps.logistics.models import Driver, Route
from apps.logistics.queries import group_orders_by_zone, order_weights, unassigned_orders_queryset
from apps.logistics.routes import add_orders_to_route, create_route_with_orders

SESSION_ASSIGNMENTS_KEY = "routing_zone_drivers"
NO_DRIVER_ID = 0


def assignment_date_key(ship_date: date | None) -> str:
    return ship_date.isoformat() if ship_date else "default"


def get_session_assignments(session, ship_date: date | None) -> dict[int, int]:
    """zone_id -> driver_id; 0 = создать маршрут без водителя."""
    store = session.get(SESSION_ASSIGNMENTS_KEY) or {}
    raw = store.get(assignment_date_key(ship_date)) or {}
    result = {}
    for zone_key, driver_id in raw.items():
        try:
            zid = int(zone_key)
            did = int(driver_id)
        except (TypeError, ValueError):
            continue
        if did >= 0:
            result[zid] = did
    return result


def set_session_assignment(session, ship_date: date | None, zone_id: int, driver_id: int | None) -> None:
    store = session.get(SESSION_ASSIGNMENTS_KEY) or {}
    date_key = assignment_date_key(ship_date)
    day_map = dict(store.get(date_key) or {})
    key = str(int(zone_id))
    if driver_id is None:
        day_map.pop(key, None)
    elif driver_id >= 0:
        day_map[key] = int(driver_id)
    else:
        day_map.pop(key, None)
    store[date_key] = day_map
    session[SESSION_ASSIGNMENTS_KEY] = store
    session.modified = True


def clear_session_assignments(session, ship_date: date | None, zone_ids: Iterable[int]) -> None:
    store = session.get(SESSION_ASSIGNMENTS_KEY) or {}
    date_key = assignment_date_key(ship_date)
    day_map = dict(store.get(date_key) or {})
    for zone_id in zone_ids:
        day_map.pop(str(int(zone_id)), None)
    store[date_key] = day_map
    session[SESSION_ASSIGNMENTS_KEY] = store
    session.modified = True


def routing_rows(ship_date: date, session) -> tuple[list[dict], dict[int, int], Decimal]:
    orders = list(unassigned_orders_queryset(ship_date))
    weights = order_weights(orders)
    rows = group_orders_by_zone(orders, weights)
    assignments = get_session_assignments(session, ship_date)
    total_weight = Decimal("0")
    for row in rows:
        row["driver_id"] = assignments.get(row["zone_id"])
        total_weight += row["weight"] or Decimal("0")
    return rows, assignments, total_weight


def create_routes_from_zone_assignments(
    *,
    ship_date: date,
    assignments: dict[int, int],
    zone_rows: list[dict],
) -> list[Route]:
    """Один маршрут на водителя; зоны «Без водителя» — по одному маршруту на зону."""
    if not assignments:
        raise WarehouseError("Назначьте водителя или «Без водителя» на зоны")

    by_driver: dict[int, list[int]] = defaultdict(list)
    no_driver_zones: list[dict] = []
    for row in zone_rows:
        if row["zone_id"] not in assignments:
            continue
        driver_id = assignments[row["zone_id"]]
        if driver_id == NO_DRIVER_ID:
            no_driver_zones.append(row)
        else:
            by_driver[driver_id].extend(row["order_ids"])

    if not by_driver and not no_driver_zones:
        raise WarehouseError("Нет зон для создания маршрута")

    drivers = {
        driver.id: driver
        for driver in Driver.objects.filter(pk__in=by_driver.keys(), is_active=True).select_related("vehicle")
    }
    created = []
    for driver_id, order_ids in by_driver.items():
        driver = drivers.get(driver_id)
        if driver is None:
            raise WarehouseError("Водитель не найден или неактивен")
        unique_ids = list(dict.fromkeys(order_ids))
        if not unique_ids:
            continue
        route = create_route_with_orders(
            route_date=ship_date,
            driver=driver,
            order_ids=unique_ids,
            name=driver.full_name,
            status=Route.Status.DRAFT,
        )
        created.append(route)

    for row in no_driver_zones:
        unique_ids = list(dict.fromkeys(row["order_ids"]))
        if not unique_ids:
            continue
        route = create_route_with_orders(
            route_date=ship_date,
            driver=None,
            order_ids=unique_ids,
            name=row["zone_name"],
            status=Route.Status.DRAFT,
        )
        created.append(route)

    if not created:
        raise WarehouseError("Не удалось создать маршруты")
    return created


def add_assigned_orders_to_route(
    *,
    route: Route,
    assignments: dict[int, int],
    zone_rows: list[dict],
) -> int:
    if route.status not in Route.LIVE_STATUSES:
        raise WarehouseError("Можно добавлять только в черновик или запланированный маршрут")
    order_ids = []
    if route.driver_id:
        for row in zone_rows:
            if assignments.get(row["zone_id"]) == route.driver_id:
                order_ids.extend(row["order_ids"])
        if not order_ids:
            raise WarehouseError("Нет заказов для этого водителя")
    else:
        for row in zone_rows:
            if assignments.get(row["zone_id"]) == NO_DRIVER_ID:
                order_ids.extend(row["order_ids"])
        if not order_ids:
            raise WarehouseError("Отметьте зоны как «Без водителя»")
    unique_ids = list(dict.fromkeys(order_ids))
    return add_orders_to_route(route, unique_ids)


def assignment_zone_ids_for_route(route: Route, assignments: dict[int, int], zone_rows: list[dict]) -> list[int]:
    if route.driver_id:
        target = route.driver_id
    else:
        target = NO_DRIVER_ID
    return [row["zone_id"] for row in zone_rows if assignments.get(row["zone_id"]) == target]
