from decimal import Decimal

from django.db.models import Sum

from apps.common.kg import as_kg
from apps.inventory.models import Movement


def plan_kg(line) -> Decimal:
    if line.reserve_kg is not None:
        return as_kg(line.reserve_kg)
    return as_kg(line.qty_kg)


def _picked_pallets():
    return Movement.objects.filter(kind="pick").values("pallet_id")


def assigned_by_order_line(line_ids=None) -> dict[int, Decimal]:
    query = Movement.objects.filter(kind="in", pallet__reserved_line_id__isnull=False)
    if line_ids is not None:
        query = query.filter(pallet__reserved_line_id__in=list(line_ids))
    rows = query.values("pallet__reserved_line_id").annotate(total=Sum("qty_kg"))
    return {row["pallet__reserved_line_id"]: as_kg(row["total"] or 0) for row in rows}


def holding_by_grade(line_ids=None) -> dict[int, list[tuple[int, Decimal]]]:
    query = (
        Movement.objects.filter(kind="in", pallet__reserved_line_id__isnull=False)
        .exclude(pallet_id__in=_picked_pallets())
    )
    if line_ids is not None:
        query = query.filter(pallet__reserved_line_id__in=list(line_ids))
    rows = query.values("pallet__reserved_line_id", "lot__grade_id").annotate(total=Sum("qty_kg"))
    found: dict[int, list[tuple[int, Decimal]]] = {}
    for row in rows:
        found.setdefault(row["pallet__reserved_line_id"], []).append(
            (row["lot__grade_id"], as_kg(row["total"] or 0))
        )
    return found


def holding_by_order_line(line_ids=None) -> dict[int, Decimal]:
    query = (
        Movement.objects.filter(kind="in", pallet__reserved_line_id__isnull=False)
        .exclude(pallet_id__in=_picked_pallets())
    )
    if line_ids is not None:
        query = query.filter(pallet__reserved_line_id__in=list(line_ids))
    rows = query.values("pallet__reserved_line_id").annotate(total=Sum("qty_kg"))
    return {row["pallet__reserved_line_id"]: as_kg(row["total"] or 0) for row in rows}


def client_reserved_pools() -> dict[int, list[dict]]:
    rows = (
        Movement.objects.filter(
            kind="in",
            pallet__reserved_client_id__isnull=False,
            pallet__reserved_line_id__isnull=True,
            pallet__order_id__isnull=True,
        )
        .exclude(pallet_id__in=_picked_pallets())
        .select_related("lot__caliber", "pallet")
    )
    pools: dict[int, list[dict]] = {}
    for movement in rows:
        lot = movement.lot
        pools.setdefault(movement.pallet.reserved_client_id, []).append(
            {
                "product_id": lot.product_id,
                "caliber": lot.caliber,
                "grade_id": lot.grade_id,
                "kg": as_kg(movement.qty_kg),
            }
        )
    return pools


def legacy_reserved_pools() -> dict[int, list[dict]]:
    rows = (
        Movement.objects.filter(kind="in", pallet__order_id__isnull=False, pallet__reserved_line_id__isnull=True)
        .exclude(pallet_id__in=_picked_pallets())
        .select_related("lot__caliber", "pallet")
    )
    pools: dict[int, list[dict]] = {}
    for movement in rows:
        lot = movement.lot
        pools.setdefault(movement.pallet.order_id, []).append(
            {
                "product_id": lot.product_id,
                "caliber": lot.caliber,
                "grade_id": lot.grade_id,
                "kg": as_kg(movement.qty_kg),
            }
        )
    return pools
