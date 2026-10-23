from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Caliber, ConsigneeCategory, Grade, Packaging
from apps.common.constants import PALLET_TARE
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.kg import as_kg
from apps.common.numbers import barcode, document_number
from apps.inventory.services import has_left, post_in, remove_in, update_in
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
        receipt=receipt,
        product_id=product_id,
        caliber_id=caliber_id,
        grade_id=grade_id,
        source=Lot.Source.SUPPLY,
        parent__isnull=True,
        is_opening=False,
    ).first()
    if lot is not None:
        return lot
    lot = Lot.objects.create(
        receipt=receipt,
        supplier_id=receipt.supplier_id,
        product_id=product_id,
        caliber_id=caliber_id,
        grade_id=grade_id,
        received_on=receipt.doc_date,
        source=Lot.Source.SUPPLY,
    )
    lot.number = document_number("ПТ", lot.id)
    lot.save(update_fields=["number"])
    return lot


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


def _category_ids(raw: list[int] | None) -> list[int]:
    if not raw:
        return []
    return list(ConsigneeCategory.objects.filter(pk__in=raw).order_by("sort_order", "name").values_list("id", flat=True))


def _reserve_fits(customer_line, category_ids: list[int]) -> None:
    if not category_ids:
        return
    order = customer_line.order
    consignee = order.consignee if order is not None else None
    if consignee is None or consignee.category_id not in category_ids:
        raise WarehouseError("Заказ этой категории не может взять паллету")


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
    consignee_id: int | None = None,
    category_ids: list[int] | None = None,
):
    task, line = _open_line(task_line_id, "receive")
    caliber, grade = _fact(caliber_id, grade_id)
    packaging = get_row(Packaging, packaging_id, "тара")
    gross, net = _resolve_weight(places, pallet_type, gross_kg, net_kg, packaging.tare_kg, net_manual)
    receipt = _ensure_receipt(task, user)
    lot = _lot(receipt, line.product_id, caliber.id, grade.id)
    customer_line = None
    order_id = None
    if task.order_id and line.order_line_id:
        customer_line = line.order_line
        order_id = customer_line.order_id
    elif reserve and line.purchase_line_id and line.purchase_line.customer_line_id:
        customer_line = line.purchase_line.customer_line
        order_id = customer_line.order_id
    consignee = None
    if order_id is None and consignee_id:
        from apps.catalog.models import Consignee

        consignee = get_row(Consignee, consignee_id, "грузополучатель")
    chosen = _category_ids(category_ids)
    if customer_line is not None:
        _reserve_fits(customer_line, chosen)
    pallet = Pallet.objects.create(
        kind="inbound",
        status="closed",
        pallet_type=pallet_type,
        order_id=order_id,
        reserved_consignee=consignee,
        reserved_line=customer_line,
        created_by=user,
        closed_by=user,
        closed_at=timezone.now(),
    )
    pallet.number = document_number("ПЛ", pallet.id)
    pallet.barcode = barcode("PL", pallet.id)
    pallet.save(update_fields=["number", "barcode"])
    if chosen:
        pallet.allowed_categories.set(chosen)
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
        reserved=order_id is not None or consignee is not None,
    )
    from apps.planning.services import refresh_receive_status

    refresh_receive_status(task)
    from apps.printing.services import schedule_accepted_passport

    schedule_accepted_passport(pallet.id, getattr(user, "id", None))
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
    from apps.planning.services import refresh_receive_status

    refresh_receive_status(task)
    return accepted


def _editable(movement_id: int):
    from apps.inventory.models import Movement

    movement = get_row(Movement, movement_id, "движение")
    if movement.kind != "in":
        raise WarehouseError("Правится только приход")
    if has_left(movement):
        raise WarehouseError("По паллете уже есть отбор, отгрузка или переработка")
    if movement.task_line_id is None:
        raise WarehouseError("Этот приход проведён документом переработки")
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
    """
    Возвращает словарь product_id -> настройки по умолчанию для приёмки.
    По умолчанию берет настройки с ПОСЛЕДНЕЙ принятой паллеты в этом задании,
    чтобы кладовщику не приходилось каждый раз выбирать тару и сорт.
    """
    from apps.inventory.models import Movement

    # Ищем последний приход в рамках этого задания
    query = Movement.objects.filter(kind="in", task=task).select_related(
        "pallet", "lot"
    ).prefetch_related(
        "pallet__units__packaging", "pallet__allowed_categories"
    ).order_by("-id")

    found = {}
    for movement in query:
        product_id = movement.lot.product_id
        if product_id in found:
            continue
            
        unit = movement.pallet.units.first()
        categories = list(movement.pallet.allowed_categories.values_list("id", flat=True))
        
        found[product_id] = {
            "pallet_type": movement.pallet.pallet_type or "",
            "packaging_id": unit.packaging_id if unit else None,
            "caliber_id": movement.lot.caliber_id,
            "grade_id": movement.lot.grade_id,
            "category_ids": categories,
        }
        
    return found


@transaction.atomic
def delete_inbound(movement_id: int) -> None:
    movement, pallet, _receipt = _editable(movement_id)
    task = movement.task
    remove_in(movement)
    pallet.units.all().delete()
    pallet.delete()
    if task is not None:
        from apps.planning.services import refresh_receive_status

        refresh_receive_status(task)
