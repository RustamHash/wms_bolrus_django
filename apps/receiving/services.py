from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Caliber, Grade, Packaging
from apps.common.constants import PALLET_TARE
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.kg import as_kg
from apps.common.numbers import barcode, document_number
from apps.inventory.services import has_downstream, post_in, remove_in, update_in
from apps.planning.models import Task, TaskLine
from apps.receiving.models import Lot, Receipt
from apps.storage.models import HandlingUnit, Pallet


def _open_line(task_line_id: int, kind: str) -> tuple[Task, TaskLine]:
    line = get_row(TaskLine, task_line_id, "строка задания")
    task = line.task
    if task.kind != kind:
        raise WarehouseError("Задание другого вида")
    if not task.is_working():
        raise WarehouseError("Задание не к приемке")
    return task, line


def _ensure_receipt(task: Task, user) -> Receipt:
    if task.receipt_id:
        receipt = Receipt.objects.filter(pk=task.receipt_id).first()
        if receipt is not None:
            return receipt
    if task.supplier_id is None or task.doc_date is None:
        raise WarehouseError("У задания на приёмку нет поставщика или даты")
    receipt = Receipt.objects.create(
        supplier_id=task.supplier_id,
        doc_date=task.doc_date,
        ttn_number=task.ttn_number,
        created_by=user,
        status="open",
    )
    receipt.number = document_number("ПР", receipt.id)
    receipt.save(update_fields=["number"])
    task.receipt = receipt
    task.save(update_fields=["receipt"])
    return receipt


def _lot(receipt: Receipt, product_id, caliber_id, grade_id) -> Lot:
    lot = Lot.objects.filter(
        receipt=receipt, product_id=product_id, caliber_id=caliber_id, grade_id=grade_id
    ).first()
    if lot is not None:
        return lot
    return Lot.objects.create(
        receipt=receipt,
        supplier_id=receipt.supplier_id,
        product_id=product_id,
        caliber_id=caliber_id,
        grade_id=grade_id,
        received_on=receipt.doc_date,
    )


def _parts(total: Decimal, count: int) -> list[Decimal]:
    total = as_kg(total)
    each = (total / Decimal(count)).quantize(Decimal("0.001"))
    parts = [each] * (count - 1)
    last = as_kg(total - each * (count - 1))
    parts.append(last)
    if any(part <= 0 for part in parts):
        raise WarehouseError("Слишком много упаковок для этого веса")
    return parts


def _places(*, receipt, lot, pallet, packaging, places: int, gross_kg, net_kg, grade_id) -> Decimal:
    gross_parts = _parts(gross_kg, places)
    net_parts = _parts(net_kg, places)
    for gross, net in zip(gross_parts, net_parts):
        unit = HandlingUnit.objects.create(
            receipt=receipt,
            lot=lot,
            packaging=packaging,
            gross_kg=gross,
            net_kg=net,
            grade_id=grade_id,
            pallet=pallet,
            status="received",
        )
        unit.barcode = barcode("HU", unit.id)
        unit.save(update_fields=["barcode"])
    return as_kg(net_kg)


def _fact(caliber_id: int, grade_id: int):
    return get_row(Caliber, caliber_id, "калибр"), get_row(Grade, grade_id, "сортность")


def expected_net(gross: Decimal, places: int, pallet_type: str, package_tare) -> Decimal:
    pallet = as_kg(PALLET_TARE[pallet_type])
    packages = as_kg(package_tare) * places
    return as_kg(gross - pallet - packages)


def _resolve_weight(places: int, pallet_type: str, gross_kg, net_kg, package_tare, manual: bool):
    if places < 1 or places > 500:
        raise WarehouseError("Укажите количество упаковок")
    if pallet_type not in PALLET_TARE:
        raise WarehouseError("Укажите вид паллета")
    gross = as_kg(gross_kg)
    if gross <= 0:
        raise WarehouseError("Укажите грязный вес")
    net = as_kg(net_kg) if manual else expected_net(gross, places, pallet_type, package_tare)
    if net <= 0 or net > gross:
        raise WarehouseError("Чистый вес должен быть больше нуля и не больше грязного")
    return gross, net


@transaction.atomic
def accept_pallet(
    *,
    task_line_id: int,
    packaging_id: int,
    places: int,
    pallet_type: str,
    gross_kg,
    net_kg=None,
    net_manual: bool = False,
    caliber_id: int,
    grade_id: int,
    user,
    reserve: bool = False,
    client_id: int | None = None,
):
    task, line = _open_line(task_line_id, "receive")
    caliber, grade = _fact(caliber_id, grade_id)
    packaging = get_row(Packaging, packaging_id, "тара")
    gross, net = _resolve_weight(places, pallet_type, gross_kg, net_kg, packaging.tare_kg, net_manual)
    receipt = _ensure_receipt(task, user)
    lot = _lot(receipt, line.product_id, caliber.id, grade.id)
    customer_line = None
    order_id = None
    if task.order_id:
        customer_line = line.order_line
        order_id = task.order_id
    elif reserve and line.purchase_line_id and line.purchase_line.customer_line_id:
        customer_line = line.purchase_line.customer_line
        order_id = customer_line.order_id
    client = None
    if order_id is None and client_id:
        from apps.catalog.models import Client

        client = get_row(Client, client_id, "клиент")
    pallet = Pallet.objects.create(
        kind="inbound",
        status="closed",
        pallet_type=pallet_type,
        order_id=order_id,
        reserved_client=client,
        reserved_line=customer_line,
        created_by=user,
        closed_by=user,
        closed_at=timezone.now(),
    )
    pallet.number = document_number("ПЛ", pallet.id)
    pallet.barcode = barcode("PL", pallet.id)
    pallet.save(update_fields=["number", "barcode"])
    _places(
        receipt=receipt,
        lot=lot,
        pallet=pallet,
        packaging=packaging,
        places=places,
        gross_kg=gross,
        net_kg=net,
        grade_id=grade.id,
    )
    movement = post_in(
        task=task,
        task_line=line,
        pallet=pallet,
        lot=lot,
        qty_kg=net,
        user=user,
        reserved=order_id is not None or client is not None,
    )
    return pallet, movement


@transaction.atomic
def accept_loose(*, task_id: int, rows: list[dict], user):
    from apps.common.kg import format_kg

    accepted = []
    for row in rows:
        qty = as_kg(row["qty_kg"])
        if qty <= 0:
            continue
        task, line = _open_line(row["task_line_id"], "receive")
        if task.id != task_id:
            raise WarehouseError("Строка относится к другому заданию")
        caliber, grade = _fact(row["caliber_id"], row["grade_id"])
        receipt = _ensure_receipt(task, user)
        lot = _lot(receipt, line.product_id, caliber.id, grade.id)
        pallet = Pallet.objects.create(
            kind="loose",
            status="closed",
            pallet_type="",
            created_by=user,
            closed_by=user,
            closed_at=timezone.now(),
        )
        pallet.number = document_number("БП", pallet.id)
        pallet.save(update_fields=["number"])
        post_in(task=task, task_line=line, pallet=pallet, lot=lot, qty_kg=qty, user=user)
        accepted.append(f"{line.product.name} {format_kg(qty)} кг")
    if not accepted:
        raise WarehouseError("Укажите количество")
    return accepted


def _editable(movement_id: int):
    from apps.inventory.models import Movement

    movement = get_row(Movement, movement_id, "движение")
    if movement.kind != "in":
        raise WarehouseError("Правится только приход")
    if has_downstream(movement):
        raise WarehouseError("По паллете уже есть отбор или отгрузка")
    pallet = movement.pallet
    if pallet.kind == "loose":
        return movement, pallet, movement.lot.receipt
    unit = pallet.units.select_related("receipt").first()
    if unit is None:
        raise WarehouseError("У паллеты нет грузомест")
    return movement, pallet, unit.receipt


@transaction.atomic
def edit_inbound(
    movement_id: int,
    *,
    caliber_id,
    grade_id,
    packaging_id,
    places,
    pallet_type,
    gross_kg,
    net_kg=None,
    net_manual: bool = False,
):
    movement, pallet, receipt = _editable(movement_id)
    caliber, grade = _fact(caliber_id, grade_id)
    packaging = get_row(Packaging, packaging_id, "тара")
    gross, net = _resolve_weight(places, pallet_type, gross_kg, net_kg, packaging.tare_kg, net_manual)
    pallet.units.all().delete()
    pallet.pallet_type = pallet_type
    pallet.save(update_fields=["pallet_type"])
    lot = _lot(receipt, movement.task_line.product_id, caliber.id, grade.id)
    _places(
        receipt=receipt,
        lot=lot,
        pallet=pallet,
        packaging=packaging,
        places=places,
        gross_kg=gross,
        net_kg=net,
        grade_id=grade.id,
    )
    return update_in(movement, lot=lot, qty_kg=net)


def pallet_defaults(task: Task) -> dict[int, dict]:
    from apps.inventory.models import Movement

    query = Movement.objects.filter(kind="in").select_related("pallet", "lot").prefetch_related("pallet__units")
    if task.purchase_order_id:
        query = query.filter(task__purchase_order_id=task.purchase_order_id)
    else:
        query = query.filter(task_id=task.id)
    found: dict[int, dict] = {}
    for movement in query.order_by("-id"):
        product_id = movement.lot.product_id
        if product_id in found:
            continue
        unit = movement.pallet.units.all().first()
        found[product_id] = {
            "pallet_type": movement.pallet.pallet_type or "",
            "packaging_id": unit.packaging_id if unit else None,
        }
    return found


@transaction.atomic
def delete_inbound(movement_id: int) -> None:
    movement, pallet, _receipt = _editable(movement_id)
    remove_in(movement)
    pallet.units.all().delete()
    pallet.delete()
