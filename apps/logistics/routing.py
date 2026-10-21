"""Маршрутизация: зона → водитель, заказ может уйти другому водителю."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Iterable

from apps.common.exceptions import WarehouseError
from apps.logistics.models import Driver, Route
from apps.logistics.queries import group_orders_by_zone, order_weights, unassigned_orders_queryset
from apps.logistics.routes import add_orders_to_route, create_route_with_orders

SESSION_ASSIGNMENTS_KEY = "routing_zone_drivers"
SESSION_ORDER_ASSIGNMENTS_KEY = "routing_order_drivers"
NO_DRIVER_ID = 0


def assignment_date_key(ship_date: date | None) -> str:
    return ship_date.isoformat() if ship_date else "default"


def _read_id_map(session, store_key: str, ship_date: date | None) -> dict[int, int]:
    store = session.get(store_key) or {}
    raw = store.get(assignment_date_key(ship_date)) or {}
    result = {}
    for raw_id, driver_id in raw.items():
        try:
            item_id = int(raw_id)
            did = int(driver_id)
        except (TypeError, ValueError):
            continue
        if did >= 0:
            result[item_id] = did
    return result


def _write_id(session, store_key: str, ship_date: date | None, item_id: int, driver_id: int | None) -> None:
    store = session.get(store_key) or {}
    date_key = assignment_date_key(ship_date)
    day_map = dict(store.get(date_key) or {})
    key = str(int(item_id))
    if driver_id is None or driver_id < 0:
        day_map.pop(key, None)
    else:
        day_map[key] = int(driver_id)
    store[date_key] = day_map
    session[store_key] = store
    session.modified = True


def _drop_ids(session, store_key: str, ship_date: date | None, item_ids: Iterable[int]) -> None:
    store = session.get(store_key) or {}
    date_key = assignment_date_key(ship_date)
    day_map = dict(store.get(date_key) or {})
    for item_id in item_ids:
        day_map.pop(str(int(item_id)), None)
    store[date_key] = day_map
    session[store_key] = store
    session.modified = True


def get_session_assignments(session, ship_date: date | None) -> dict[int, int]:
    """zone_id -> driver_id; 0 = создать маршрут без водителя."""
    return _read_id_map(session, SESSION_ASSIGNMENTS_KEY, ship_date)


def set_session_assignment(session, ship_date: date | None, zone_id: int, driver_id: int | None) -> None:
    _write_id(session, SESSION_ASSIGNMENTS_KEY, ship_date, zone_id, driver_id)


def clear_session_assignments(session, ship_date: date | None, zone_ids: Iterable[int]) -> None:
    _drop_ids(session, SESSION_ASSIGNMENTS_KEY, ship_date, zone_ids)


def get_session_order_assignments(session, ship_date: date | None) -> dict[int, int]:
    """order_id -> driver_id; 0 = маршрут без водителя. Нет ключа — заказ следует зоне."""
    return _read_id_map(session, SESSION_ORDER_ASSIGNMENTS_KEY, ship_date)


def set_session_order_assignment(session, ship_date: date | None, order_id: int, driver_id: int | None) -> None:
    _write_id(session, SESSION_ORDER_ASSIGNMENTS_KEY, ship_date, order_id, driver_id)


def clear_session_order_assignments(session, ship_date: date | None, order_ids: Iterable[int]) -> None:
    _drop_ids(session, SESSION_ORDER_ASSIGNMENTS_KEY, ship_date, order_ids)


def routing_rows(ship_date: date, session) -> tuple[list[dict], dict[int, int], Decimal]:
    orders = list(unassigned_orders_queryset(ship_date))
    weights = order_weights(orders)
    rows = group_orders_by_zone(orders, weights)
    assignments = get_session_assignments(session, ship_date)
    order_assignments = get_session_order_assignments(session, ship_date)
    total_weight = Decimal("0")
    for row in rows:
        row["driver_id"] = assignments.get(row["zone_id"])
        overrides = 0
        for order in row["orders"]:
            driver_id = order_assignments.get(order["id"])
            order["driver_id"] = driver_id
            order["assigned"] = driver_id is not None
            if driver_id is not None:
                overrides += 1
        row["override_count"] = overrides
        total_weight += row["weight"] or Decimal("0")
    return rows, assignments, total_weight


def driver_load_summary(rows: list[dict], drivers) -> list[dict]:
    """Вес заказов по эффективному водителю. Перегруз — если вес больше тоннажа авто."""
    weights: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[int, int] = defaultdict(int)
    for row in rows:
        zone_driver = row.get("driver_id")
        for order in row.get("orders") or []:
            driver_id = order["driver_id"] if order.get("assigned") else zone_driver
            if driver_id is None:
                continue
            weights[driver_id] += order.get("weight") or Decimal("0")
            counts[driver_id] += 1
    by_id = {driver.id: driver for driver in drivers}
    result = []
    for driver_id, weight in weights.items():
        if driver_id == NO_DRIVER_ID:
            result.append(
                {
                    "driver_id": NO_DRIVER_ID,
                    "name": "Без водителя",
                    "weight": weight,
                    "count": counts[driver_id],
                    "capacity": None,
                    "over": False,
                }
            )
            continue
        driver = by_id.get(driver_id)
        capacity = None
        if driver is not None and driver.vehicle_id and driver.vehicle and driver.vehicle.load_capacity_kg:
            capacity = driver.vehicle.load_capacity_kg
        result.append(
            {
                "driver_id": driver_id,
                "name": driver.full_name if driver is not None else f"#{driver_id}",
                "weight": weight,
                "count": counts[driver_id],
                "capacity": capacity,
                "over": capacity is not None and weight > capacity,
            }
        )
    result.sort(key=lambda item: (item["name"] == "Без водителя", item["name"].lower()))
    return result


def _row_orders(row: dict) -> list[dict]:
    orders = row.get("orders")
    if orders is not None:
        return orders
    return [{"id": order_id} for order_id in row.get("order_ids") or []]


def _order_override(order: dict) -> int | None:
    """Явный водитель заказа. None — заказ следует зоне."""
    if order.get("assigned"):
        return order.get("driver_id")
    if "assigned" in order:
        return None
    return order.get("driver_id")


def partition_route_orders(zone_rows: list[dict]) -> tuple[dict[int, list[int]], list[dict], list[int]]:
    """Заказы с явным водителем идут ему, остальные — водителю зоны.

    Возвращает заказы по водителям, маршруты «без водителя» по зонам
    и зоны, чьё назначение реально использовано.
    """
    by_driver: dict[int, list[int]] = defaultdict(list)
    no_driver_zones: list[dict] = []
    inherited_zone_ids: list[int] = []
    for row in zone_rows:
        zone_driver = row.get("driver_id")
        no_driver_ids: list[int] = []
        used_inheritance = False
        for order in _row_orders(row):
            explicit = _order_override(order)
            driver_id = explicit if explicit is not None else zone_driver
            if driver_id is None:
                continue
            if explicit is None:
                used_inheritance = True
            if driver_id == NO_DRIVER_ID:
                no_driver_ids.append(order["id"])
            else:
                by_driver[driver_id].append(order["id"])
        if no_driver_ids:
            no_driver_zones.append(
                {
                    "zone_id": row["zone_id"],
                    "zone_name": row["zone_name"],
                    "order_ids": no_driver_ids,
                }
            )
        if used_inheritance and zone_driver is not None:
            inherited_zone_ids.append(row["zone_id"])
    return by_driver, no_driver_zones, inherited_zone_ids


def consumed_for_create(zone_rows: list[dict]) -> tuple[list[int], list[int]]:
    by_driver, no_driver_zones, inherited_zone_ids = partition_route_orders(zone_rows)
    order_ids = [order_id for ids in by_driver.values() for order_id in ids]
    for zone in no_driver_zones:
        order_ids.extend(zone["order_ids"])
    return order_ids, inherited_zone_ids


def consumed_for_route(route: Route, zone_rows: list[dict]) -> tuple[list[int], list[int]]:
    target = route.driver_id if route.driver_id else NO_DRIVER_ID
    by_driver, no_driver_zones, inherited_zone_ids = partition_route_orders(zone_rows)
    if target == NO_DRIVER_ID:
        order_ids = [order_id for zone in no_driver_zones for order_id in zone["order_ids"]]
    else:
        order_ids = list(by_driver.get(target, []))
    inherited = set(inherited_zone_ids)
    zone_ids = [
        row["zone_id"]
        for row in zone_rows
        if row["zone_id"] in inherited and row.get("driver_id") == target
    ]
    return order_ids, zone_ids


def _zone_label(names: list[str]) -> str:
    unique = []
    for name in names:
        cleaned = (name or "").strip() or "Без зоны"
        if cleaned not in unique:
            unique.append(cleaned)
    return ", ".join(unique)[:255]


def _driver_zone_names(zone_rows: list[dict]) -> dict[int, list[str]]:
    names: dict[int, list[str]] = defaultdict(list)
    for row in zone_rows:
        zone_driver = row.get("driver_id")
        label = (row.get("zone_name") or "").strip() or "Без зоны"
        seen = set()
        for order in _row_orders(row):
            explicit = _order_override(order)
            driver_id = explicit if explicit is not None else zone_driver
            if driver_id is None or driver_id == NO_DRIVER_ID or driver_id in seen:
                continue
            seen.add(driver_id)
            names[driver_id].append(label)
    return names


def create_routes_from_zone_assignments(
    *,
    ship_date: date,
    zone_rows: list[dict],
) -> list[Route]:
    """Один маршрут на водителя; «Без водителя» — по одному маршруту на зону."""
    by_driver, no_driver_zones, _inherited = partition_route_orders(zone_rows)
    if not by_driver and not no_driver_zones:
        raise WarehouseError("Назначьте водителя или «Без водителя» на зоны или заказы")

    drivers = {
        driver.id: driver
        for driver in Driver.objects.filter(pk__in=by_driver.keys(), is_active=True).select_related("vehicle")
    }
    zone_names = _driver_zone_names(zone_rows)
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
            name=_zone_label(zone_names.get(driver_id, [])) or driver.full_name,
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
    zone_rows: list[dict],
) -> int:
    if route.status not in Route.LIVE_STATUSES:
        raise WarehouseError("Можно добавлять только в черновик или запланированный маршрут")
    order_ids, _zone_ids = consumed_for_route(route, zone_rows)
    if route.driver_id:
        if not order_ids:
            raise WarehouseError("Нет заказов для этого водителя")
    elif not order_ids:
        raise WarehouseError("Отметьте зоны или заказы как «Без водителя»")
    return add_orders_to_route(route, list(dict.fromkeys(order_ids)))
