from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Grade
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.kg import as_kg
from apps.common.numbers import barcode, document_number
from apps.inventory.services import inbound_for_pallet
from apps.planning.models import Order
from apps.shipping.models import Allocation
from apps.storage.models import HandlingUnit, Pallet


def active_units(pallet: Pallet) -> list[HandlingUnit]:
    return [unit for unit in pallet.units.all() if unit.status != "rejected"]


def unit_key(unit: HandlingUnit) -> tuple:
    lot = unit.lot
    return (lot.product_id, lot.caliber_id, unit.grade_id)


def allocation_of(unit: HandlingUnit):
    return Allocation.objects.filter(unit_id=unit.id).select_related("order_line").first()


def _status_after_detach(pallet: Pallet, unit: HandlingUnit) -> str:
    if unit.status == "allocated":
        return "allocated"
    if pallet.status == "closed":
        return "in_stock"
    return "received"


def _locked_by_movement(unit: HandlingUnit) -> None:
    if unit.pallet_id and inbound_for_pallet(unit.pallet_id) is not None:
        raise WarehouseError("Эту паллету меняют в задании: правка идёт через движение прихода")


@transaction.atomic
def create_inbound_pallet(*, user, location: str = "", cell=None) -> Pallet:
    if cell is not None:
        location = cell.label()
    pallet = Pallet.objects.create(
        kind="inbound",
        status="open",
        location=location.strip(),
        cell=cell,
        created_by=user,
    )
    pallet.number = document_number("ПЛ", pallet.id)
    pallet.barcode = barcode("PL", pallet.id)
    pallet.save(update_fields=["number", "barcode"])
    return pallet


@transaction.atomic
def create_outbound_pallet(*, order_id: int, user, location: str = "") -> Pallet:
    order = get_row(Order, order_id, "заказ")
    pallet = Pallet.objects.create(
        kind="outbound",
        status="open",
        location=location.strip(),
        order=order,
        created_by=user,
    )
    pallet.number = document_number("ПЛ", pallet.id)
    pallet.barcode = barcode("PL", pallet.id)
    pallet.save(update_fields=["number", "barcode"])
    return pallet


def add_to_inbound(pallet: Pallet, unit: HandlingUnit) -> None:
    if inbound_for_pallet(pallet.id) is not None:
        raise WarehouseError("Принятую паллету меняют через движение задания")
    if pallet.kind != "inbound":
        raise WarehouseError("На входную паллету нельзя положить место с выхода")
    if pallet.status != "open":
        raise WarehouseError("Входная паллета закрыта. Откройте следующую")
    if unit.status in ("shipped", "rejected"):
        raise WarehouseError("Это место нельзя положить на паллету")
    if unit.pallet_id and unit.pallet_id != pallet.id:
        raise WarehouseError("Место уже лежит на другой паллете")
    keys = {unit_key(item) for item in active_units(pallet) if item.id != unit.id}
    if len(keys) > 1 or (keys and unit_key(unit) not in keys):
        raise WarehouseError(
            "На входе паллета только однородная: товар, калибр и сортность должны совпасть. Закройте паллету и откройте следующую"
        )
    unit.pallet = pallet
    if unit.status != "allocated":
        unit.status = "on_pallet"
    unit.save(update_fields=["pallet", "status"])


def add_to_outbound(pallet: Pallet, unit: HandlingUnit) -> None:
    if pallet.kind != "outbound":
        raise WarehouseError("Это не отгрузочная паллета")
    if pallet.status != "open":
        raise WarehouseError("Отгрузочная паллета закрыта")
    allocation = allocation_of(unit)
    if unit.status != "allocated" or allocation is None:
        raise WarehouseError("На выход кладут только места, уже назначенные на заказ")
    if allocation.order_line.order_id != pallet.order_id:
        raise WarehouseError("Место назначено другому заказу")
    if unit.pallet_id and unit.pallet and unit.pallet.kind == "outbound" and unit.pallet_id != pallet.id:
        raise WarehouseError("Место уже на другой отгрузочной паллете")
    unit.pallet = pallet
    unit.save(update_fields=["pallet"])


def detach_unit(unit: HandlingUnit) -> None:
    _locked_by_movement(unit)
    if unit.pallet_id is None:
        return
    pallet = unit.pallet
    if unit.status == "shipped":
        raise WarehouseError("Отгруженное место нельзя снять с паллеты")
    unit.status = _status_after_detach(pallet, unit)
    unit.pallet = None
    unit.save(update_fields=["status", "pallet"])


def close_pallet(pallet: Pallet, user) -> None:
    if pallet.status == "closed":
        return
    units = active_units(pallet)
    if pallet.kind == "inbound" and not units:
        raise WarehouseError("Нельзя закрыть пустую паллету")
    for unit in units:
        if pallet.kind == "inbound" and unit.status == "on_pallet":
            unit.status = "in_stock"
            unit.save(update_fields=["status"])
    pallet.status = "closed"
    pallet.closed_at = timezone.now()
    pallet.closed_by = user
    pallet.save(update_fields=["status", "closed_at", "closed_by"])


def change_grade(unit: HandlingUnit, grade_id: int) -> None:
    _locked_by_movement(unit)
    if unit.status in ("shipped", "rejected"):
        raise WarehouseError("У этого места нельзя менять сортность")
    if unit.pallet_id and unit.pallet.kind == "inbound":
        raise WarehouseError("Снимите место с входной паллеты, затем меняйте сортность")
    if allocation_of(unit) is not None:
        raise WarehouseError("Снимите назначение на заказ, затем меняйте сортность")
    grade = get_row(Grade, grade_id, "сортность")
    unit.grade = grade
    unit.save(update_fields=["grade"])


def reject_unit(unit: HandlingUnit, reason: str) -> None:
    _locked_by_movement(unit)
    if unit.status == "shipped":
        raise WarehouseError("Отгруженное место нельзя отправить в брак")
    if unit.status == "rejected":
        return
    if unit.pallet_id and unit.pallet.kind == "inbound":
        raise WarehouseError("Снимите место с входной паллеты, затем отметьте брак")
    if allocation_of(unit) is not None:
        raise WarehouseError("Снимите назначение на заказ, затем отметьте брак")
    unit.status = "rejected"
    unit.reject_reason = reason.strip()
    unit.pallet = None
    unit.save(update_fields=["status", "reject_reason", "pallet"])


def stock_place_rows() -> list[dict]:
    units = HandlingUnit.objects.filter(status__in=("received", "on_pallet", "in_stock", "allocated")).select_related(
        "lot__product", "lot__caliber", "grade"
    )
    buckets: dict[tuple, dict] = {}
    for unit in units:
        lot = unit.lot
        key = (lot.product.name, lot.caliber.name, unit.grade.name)
        row = buckets.setdefault(
            key,
            {
                "product": key[0],
                "caliber": key[1],
                "grade": key[2],
                "free_kg": Decimal("0.000"),
                "allocated_kg": Decimal("0.000"),
                "places": 0,
            },
        )
        kg = as_kg(unit.net_kg)
        row["places"] += 1
        if unit.status == "allocated":
            row["allocated_kg"] += kg
        else:
            row["free_kg"] += kg
    return [buckets[key] for key in sorted(buckets)]


def find_by_barcode(code: str):
    raw = (code or "").strip()
    pieces = [raw]
    first = raw.splitlines()[0].strip() if raw else ""
    if first and first != raw:
        pieces.append(first)
    for piece in pieces:
        normalized = piece.upper()
        if not normalized:
            continue
        unit = HandlingUnit.objects.filter(barcode=normalized).first()
        if unit:
            return ("unit", unit)
        pallet = Pallet.objects.filter(barcode=normalized).first() or Pallet.objects.filter(number=piece).first()
        if pallet:
            return ("pallet", pallet)
    return (None, None)
