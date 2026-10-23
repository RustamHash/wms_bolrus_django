from decimal import Decimal

from apps.common.kg import as_kg
from apps.inventory.models import Movement
from apps.inventory.services import available_kg_by_pallet, reworked_pallet_ids


def plan_kg(line) -> Decimal:
    if line.reserve_kg is not None:
        return as_kg(line.reserve_kg)
    return as_kg(line.qty_kg)


def _kept_kg(rows: list[dict], key: str) -> dict[int, Decimal]:
    pallet_ids = [row["pallet_id"] for row in rows]
    available = available_kg_by_pallet(pallet_ids)
    found: dict[int, Decimal] = {}
    for row in rows:
        qty = available.get(row["pallet_id"], Decimal("0.000"))
        if qty <= 0:
            continue
        ident = row[key]
        found[ident] = found.get(ident, Decimal("0.000")) + qty
    return found


def assigned_by_order_line(line_ids=None) -> dict[int, Decimal]:
    query = Movement.objects.filter(kind="in", pallet__reserved_line_id__isnull=False).exclude(
        pallet_id__in=reworked_pallet_ids()
    )
    if line_ids is not None:
        query = query.filter(pallet__reserved_line_id__in=list(line_ids))
    rows = list(query.values("pallet_id", "pallet__reserved_line_id"))
    return _kept_kg(rows, "pallet__reserved_line_id")


def holding_by_grade(line_ids=None) -> dict[int, list[tuple[int, Decimal]]]:
    query = Movement.objects.filter(kind="in", pallet__reserved_line_id__isnull=False).exclude(
        pallet_id__in=reworked_pallet_ids()
    )
    if line_ids is not None:
        query = query.filter(pallet__reserved_line_id__in=list(line_ids))
    rows = list(query.values("pallet_id", "pallet__reserved_line_id", "lot__grade_id"))
    available = available_kg_by_pallet([row["pallet_id"] for row in rows])
    found: dict[int, list[tuple[int, Decimal]]] = {}
    for row in rows:
        qty = available.get(row["pallet_id"], Decimal("0.000"))
        if qty <= 0:
            continue
        found.setdefault(row["pallet__reserved_line_id"], []).append((row["lot__grade_id"], qty))
    return found


def holding_by_order_line(line_ids=None) -> dict[int, Decimal]:
    return assigned_by_order_line(line_ids)


def client_reserved_pools() -> dict[int, list[dict]]:
    rows = list(
        Movement.objects.filter(
            kind="in",
            pallet__reserved_consignee_id__isnull=False,
            pallet__reserved_line_id__isnull=True,
            pallet__order_id__isnull=True,
        )
        .exclude(pallet_id__in=reworked_pallet_ids())
        .select_related("lot__caliber", "pallet")
    )
    available = available_kg_by_pallet([movement.pallet_id for movement in rows])
    pools: dict[int, list[dict]] = {}
    for movement in rows:
        kg = available.get(movement.pallet_id, Decimal("0.000"))
        if kg <= 0:
            continue
        lot = movement.lot
        pools.setdefault(movement.pallet.reserved_consignee_id, []).append(
            {
                "product_id": lot.product_id,
                "caliber": lot.caliber,
                "grade_id": lot.grade_id,
                "kg": kg,
            }
        )
    return pools


def open_pallet_pools() -> list[dict]:
    rows = list(
        Movement.objects.filter(kind="in")
        .exclude(pallet_id__in=reworked_pallet_ids())
        .select_related("lot__caliber", "pallet")
        .prefetch_related("pallet__allowed_categories")
        .order_by("lot__received_on", "id")
    )
    available = available_kg_by_pallet([movement.pallet_id for movement in rows])
    pools = []
    for movement in rows:
        kg = available.get(movement.pallet_id, Decimal("0.000"))
        if kg <= 0:
            continue
        pallet = movement.pallet
        categories = {item.id for item in pallet.allowed_categories.all()}
        pools.append(
            {
                "product_id": movement.lot.product_id,
                "caliber": movement.lot.caliber,
                "grade_id": movement.lot.grade_id,
                "kg": kg,
                "origin_kg": kg,
                "category_ids": categories or None,
                "reserved_line_id": pallet.reserved_line_id,
                "reserved_consignee_id": pallet.reserved_consignee_id,
                "order_id": None if pallet.reserved_line_id else pallet.order_id,
            }
        )
    return pools


def legacy_reserved_pools() -> dict[int, list[dict]]:
    rows = list(
        Movement.objects.filter(kind="in", pallet__order_id__isnull=False, pallet__reserved_line_id__isnull=True)
        .exclude(pallet_id__in=reworked_pallet_ids())
        .select_related("lot__caliber", "pallet")
    )
    available = available_kg_by_pallet([movement.pallet_id for movement in rows])
    pools: dict[int, list[dict]] = {}
    for movement in rows:
        kg = available.get(movement.pallet_id, Decimal("0.000"))
        if kg <= 0:
            continue
        lot = movement.lot
        pools.setdefault(movement.pallet.order_id, []).append(
            {
                "product_id": lot.product_id,
                "caliber": lot.caliber,
                "grade_id": lot.grade_id,
                "kg": kg,
            }
        )
    return pools
