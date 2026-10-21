from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Exists, OuterRef, Prefetch, Sum

from apps.logistics.models import Route, RouteOrder
from apps.planning.models import Order, OrderLine

NO_ZONE_ID = 0


def active_route_order_exists():
    return Exists(
        RouteOrder.objects.filter(
            order_id=OuterRef("pk"),
            route__status__in=[Route.Status.DRAFT, Route.Status.PLANNED, Route.Status.COMPLETED],
        )
    )


def unassigned_orders_queryset(ship_date: date | None = None):
    qs = (
        Order.objects.filter(status="open")
        .annotate(on_route=active_route_order_exists())
        .filter(on_route=False)
        .select_related("client", "consignee", "consignee__zone", "seller")
        .prefetch_related(Prefetch("lines", queryset=OrderLine.objects.only("id", "order_id", "qty_kg")))
        .order_by("ship_date", "id")
    )
    if ship_date is not None:
        qs = qs.filter(ship_date=ship_date)
    return qs


def order_weights(orders) -> dict[int, Decimal]:
    ids = [order.id for order in orders]
    if not ids:
        return {}
    rows = (
        OrderLine.objects.filter(order_id__in=ids)
        .values("order_id")
        .annotate(total=Sum("qty_kg"))
    )
    return {row["order_id"]: row["total"] or Decimal("0") for row in rows}


def group_orders_by_zone(orders, weights: dict[int, Decimal] | None = None) -> list[dict]:
    weights = weights or {}
    by_zone: dict[int, list] = defaultdict(list)
    for order in orders:
        zone_id = order.consignee.zone_id if order.consignee_id and order.consignee.zone_id else NO_ZONE_ID
        by_zone[zone_id].append(order)

    rows = []
    for zone_id, zone_orders in by_zone.items():
        if zone_id == NO_ZONE_ID:
            zone_name = "Без зоны"
        else:
            zone = zone_orders[0].consignee.zone
            zone_name = zone.name if zone else "Без зоны"
        addresses = {
            (order.consignee.address if order.consignee_id else "").strip()
            for order in zone_orders
        }
        addresses.discard("")
        rows.append(
            {
                "zone_id": zone_id,
                "zone_name": zone_name,
                "order_count": len(zone_orders),
                "tt_count": len(addresses) or len(zone_orders),
                "weight": sum((weights.get(order.id) or Decimal("0")) for order in zone_orders),
                "order_ids": [order.id for order in zone_orders],
            }
        )
    rows.sort(key=lambda row: (row["zone_name"] == "Без зоны", row["zone_name"].lower(), row["zone_id"]))
    return rows
