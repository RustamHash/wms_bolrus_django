from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Count, Prefetch, Sum

from apps.logistics.models import Route, RouteOrder
from apps.logistics.queries import order_weights
from apps.logistics.routes import load_local


def _routes_for_delivery_day(day: date):
    """Все маршруты с датой доставки (рейса) = day."""
    return (
        Route.objects.filter(route_date=day)
        .exclude(status=Route.Status.CANCELLED)
        .select_related("driver")
    )


def schedule_context(day: date) -> dict:
    """График по дате доставки: список, кто во сколько грузится."""
    routes = list(
        _routes_for_delivery_day(day).annotate(
            order_count=Count("route_orders", distinct=True),
            weight=Sum("route_orders__order__lines__qty_kg"),
        )
    )
    items = []
    for route in routes:
        items.append(
            {
                "id": route.id,
                "name": route.name or f"Маршрут {route.id}",
                "driver": route.driver.full_name if route.driver_id else "Без водителя",
                "when": _load_stamp(route.planned_load_at),
                "timed": bool(route.planned_load_at),
                "order_count": route.order_count or 0,
                "weight": route.weight or Decimal("0"),
                "sort": (_sort_key_plan(route.planned_load_at), route.id),
            }
        )
    items.sort(key=lambda item: item["sort"])
    for item in items:
        del item["sort"]
    return {"day": day, "items": items}


def _tt_key(order) -> str:
    if not order.consignee_id:
        return f"order:{order.id}"
    address = (order.consignee.address or "").strip()
    if address:
        return address.casefold()
    return f"consignee:{order.consignee_id}"


def _clock(value) -> str:
    if not value:
        return ""
    local = load_local(value)
    return f"{local.hour:02d}:{local.minute:02d}"


def _load_stamp(value) -> str:
    """Дата и время плана погрузки, всегда вместе."""
    if not value:
        return ""
    local = load_local(value)
    return f"{local:%d.%m.%Y} {local.hour:02d}:{local.minute:02d}"


def _plan_label(value, delivery_day: date) -> str:
    """План: время; если погрузка в другой день — с датой."""
    if not value:
        return ""
    local = load_local(value)
    clock = f"{local.hour:02d}:{local.minute:02d}"
    if local.date() != delivery_day:
        return f"{local:%d.%m} {clock}"
    return clock


def _fact_label(value, delivery_day: date) -> str:
    if not value:
        return ""
    local = load_local(value)
    clock = f"{local.hour:02d}:{local.minute:02d}"
    if local.date() != delivery_day:
        return f"{local:%d.%m} {clock}"
    return clock


def _sort_key_plan(value) -> tuple:
    """Полная дата+время плана, чтобы вечерняя погрузка шла раньше утренней."""
    if not value:
        return (1, date.max, 0, 0)
    local = load_local(value)
    return (0, local.date(), local.hour, local.minute)


def schedule_print_tree(day: date) -> dict:
    """
    Все маршруты с датой доставки = day.
    Сортировка по плану погрузки. Строки: план | факт | водитель | комментарий | вес | ТТ.
    """
    routes = list(
        _routes_for_delivery_day(day)
        .prefetch_related(
            Prefetch(
                "route_orders",
                queryset=RouteOrder.objects.select_related(
                    "order__client",
                    "order__consignee",
                ).order_by("position", "id"),
            )
        )
        .order_by("planned_load_at", "id")
    )
    all_orders = [stop.order for route in routes for stop in route.route_orders.all()]
    weights = order_weights(all_orders)

    # sort_key -> plan_label -> driver_id -> node
    by_group: dict[tuple, dict[int, dict]] = defaultdict(dict)

    for route in routes:
        plan_label = _plan_label(route.planned_load_at, day) or "Без плана"
        sort_key = _sort_key_plan(route.planned_load_at)
        group_key = (sort_key, plan_label)
        fact = _fact_label(route.actual_load_at, day)
        driver_id = route.driver_id or 0
        driver_name = route.driver.full_name if route.driver_id else "Без водителя"

        if driver_id not in by_group[group_key]:
            by_group[group_key][driver_id] = {
                "driver_id": driver_id,
                "driver_name": driver_name,
                "plan": plan_label if plan_label != "Без плана" else "",
                "facts": [],
                "notes": [],
                "weight": Decimal("0"),
                "tt": set(),
            }
        node = by_group[group_key][driver_id]
        if fact and fact not in node["facts"]:
            node["facts"].append(fact)
        note = (route.notes or "").strip()
        if note and note not in node["notes"]:
            node["notes"].append(note)

        for stop in route.route_orders.all():
            order = stop.order
            node["weight"] += weights.get(order.id) or Decimal("0")
            node["tt"].add(_tt_key(order))

    table_rows = []
    grand_weight = Decimal("0")
    grand_tt: set[str] = set()

    for group_key in sorted(by_group.keys(), key=lambda item: item[0]):
        for driver_id, node in sorted(
            by_group[group_key].items(),
            key=lambda item: (item[1]["driver_name"].casefold(), item[0]),
        ):
            table_rows.append(
                {
                    "kind": "driver",
                    "plan": node["plan"],
                    "fact": ", ".join(node["facts"]) if node["facts"] else "",
                    "label": node["driver_name"],
                    "notes": "; ".join(node["notes"]) if node["notes"] else "",
                    "weight": node["weight"],
                    "tt_count": len(node["tt"]),
                }
            )
            grand_weight += node["weight"]
            grand_tt |= node["tt"]

    return {
        "day": day,
        "rows": table_rows,
        "total_weight": grand_weight,
        "total_tt": len(grand_tt),
        "route_count": len(routes),
    }
