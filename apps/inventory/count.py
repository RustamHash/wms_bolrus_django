from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Caliber, Grade, Product, Supplier
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.kg import as_kg
from apps.common.numbers import barcode, document_number
from apps.inventory.models import InventoryDocument, InventoryLine, Movement
from apps.inventory.services import has_downstream, post_in, remove_in, update_in
from apps.planning.models import Task, TaskLine
from apps.receiving.models import Lot, Receipt
from apps.storage.models import Pallet


def parse_fact_kg(raw: str) -> Decimal:
    value_text = (raw or "").strip().replace(",", ".")
    if not value_text:
        raise WarehouseError("Укажите фактический вес. Ноль — паллеты нет")
    try:
        value = Decimal(value_text)
    except InvalidOperation as exc:
        raise WarehouseError("Некорректный вес") from exc
    value = value.quantize(Decimal("0.001"))
    if value < 0:
        raise WarehouseError("Вес не может быть отрицательным")
    return value


def stock_inbounds(product_ids: list[int] | None = None):
    """Приходы паллет, которые ещё на складе: без отбора и отгрузки."""
    taken = Movement.objects.filter(kind__in=(Movement.Kind.PICK, Movement.Kind.OUT)).values("pallet_id")
    rows = Movement.objects.filter(kind=Movement.Kind.IN).exclude(pallet_id__in=taken)
    if product_ids is not None:
        rows = rows.filter(lot__product_id__in=product_ids)
    return rows.select_related("pallet", "lot", "lot__supplier").order_by("lot__product__name", "pallet__number", "id")


@transaction.atomic
def create_document(*, doc_date, scope: str, product_ids: list[int], notes: str, user) -> InventoryDocument:
    if scope not in InventoryDocument.Scope.values:
        raise WarehouseError("Некорректный режим")
    if scope == InventoryDocument.Scope.PRODUCTS and not product_ids:
        raise WarehouseError("Отметьте номенклатуру")
    document = InventoryDocument.objects.create(
        doc_date=doc_date,
        scope=scope,
        notes=notes or "",
        created_by=user,
    )
    document.number = document_number("ИН", document.id)
    document.save(update_fields=["number"])
    if product_ids:
        document.products.set(product_ids)
    return document


@transaction.atomic
def start_document(document: InventoryDocument) -> int:
    document = InventoryDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != InventoryDocument.Status.DRAFT:
        raise WarehouseError("Начать можно только черновик")
    product_ids = None
    if document.scope == InventoryDocument.Scope.PRODUCTS:
        product_ids = list(document.products.values_list("pk", flat=True))
        if not product_ids:
            raise WarehouseError("Отметьте номенклатуру")
    lines = []
    for movement in stock_inbounds(product_ids):
        lines.append(
            InventoryLine(
                document=document,
                pallet_id=movement.pallet_id,
                lot_id=movement.lot_id,
                supplier_id=movement.lot.supplier_id,
                location=movement.pallet.location or "",
                book_kg=as_kg(movement.qty_kg),
            )
        )
    if lines:
        InventoryLine.objects.bulk_create(lines)
    document.status = InventoryDocument.Status.COUNTING
    document.save(update_fields=["status"])
    return len(lines)


def _open_line(line: InventoryLine) -> InventoryLine:
    if line.document.status != InventoryDocument.Status.COUNTING:
        raise WarehouseError("Подсчёт уже закрыт")
    return line


@transaction.atomic
def save_count(line: InventoryLine, *, fact_kg: Decimal | None, supplier_id: int, user, fact_given: bool) -> InventoryLine:
    line = InventoryLine.objects.select_for_update().select_related("document").get(pk=line.pk)
    _open_line(line)
    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if supplier is None:
        raise WarehouseError("Поставщик не найден")
    line.supplier = supplier
    fields = ["supplier"]
    if fact_given:
        fact = as_kg(fact_kg)
        if line.pallet_id is None and fact <= 0:
            raise WarehouseError("У новой паллеты вес должен быть больше нуля")
        line.fact_kg = fact
        line.counted_by = user
        line.counted_at = timezone.now()
        fields += ["fact_kg", "counted_by", "counted_at"]
    line.save(update_fields=fields)
    return line


@transaction.atomic
def add_found(
    document: InventoryDocument,
    *,
    product_id: int,
    caliber_id: int,
    grade_id: int,
    supplier_id: int,
    location: str,
    qty_kg: Decimal,
    user,
) -> InventoryLine:
    document = InventoryDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != InventoryDocument.Status.COUNTING:
        raise WarehouseError("Добавлять паллету можно только во время подсчёта")
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Укажите вес больше нуля")
    product = get_row(Product, product_id, "товар")
    caliber = get_row(Caliber, caliber_id, "калибр")
    grade = get_row(Grade, grade_id, "сорт")
    supplier = get_row(Supplier, supplier_id, "поставщик")
    if document.scope == InventoryDocument.Scope.PRODUCTS and not document.products.filter(pk=product.id).exists():
        raise WarehouseError("Этот товар не входит в инвентаризацию")
    place = (location or "").strip()
    if len(place) > 120:
        raise WarehouseError("Место длиннее 120 символов")
    return InventoryLine.objects.create(
        document=document,
        product=product,
        caliber=caliber,
        grade=grade,
        supplier=supplier,
        location=place,
        book_kg=Decimal("0.000"),
        fact_kg=qty,
        counted_by=user,
        counted_at=timezone.now(),
    )


@transaction.atomic
def delete_found(line: InventoryLine) -> None:
    line = InventoryLine.objects.select_for_update().select_related("document").get(pk=line.pk)
    _open_line(line)
    if line.pallet_id:
        raise WarehouseError("Строку снимка нельзя удалить. Поставьте факт 0, если паллеты нет")
    line.delete()


def _lot_for_supplier(source: Lot, supplier_id: int) -> Lot:
    found = (
        Lot.objects.filter(
            supplier_id=supplier_id,
            product_id=source.product_id,
            caliber_id=source.caliber_id,
            grade_id=source.grade_id,
            received_on=source.received_on,
        )
        .order_by("id")
        .first()
    )
    if found is not None:
        return found
    return Lot.objects.create(
        receipt_id=source.receipt_id,
        supplier_id=supplier_id,
        product_id=source.product_id,
        caliber_id=source.caliber_id,
        grade_id=source.grade_id,
        received_on=source.received_on,
    )


def _opening_task(document: InventoryDocument, supplier: Supplier, user):
    task = (
        Task.objects.filter(kind="receive", supplier=supplier, ttn_number=document.number)
        .select_related("receipt")
        .first()
    )
    if task is not None and task.receipt_id:
        return task, task.receipt
    receipt = Receipt.objects.create(
        supplier=supplier,
        doc_date=document.doc_date,
        ttn_number=document.number,
        status="closed",
        created_by=user,
    )
    receipt.number = document_number("ПР", receipt.id)
    receipt.save(update_fields=["number"])
    task = Task.objects.create(
        kind="receive",
        status="closed",
        supplier=supplier,
        doc_date=document.doc_date,
        ttn_number=document.number,
        receipt=receipt,
        created_by=user,
    )
    task.number = document_number("ЗД", task.id)
    task.save(update_fields=["number"])
    return task, receipt


def _lot_on_receipt(receipt: Receipt, product_id: int, caliber_id: int, grade_id: int) -> Lot:
    found = Lot.objects.filter(
        receipt=receipt, product_id=product_id, caliber_id=caliber_id, grade_id=grade_id
    ).first()
    if found is not None:
        return found
    return Lot.objects.create(
        receipt=receipt,
        supplier_id=receipt.supplier_id,
        product_id=product_id,
        caliber_id=caliber_id,
        grade_id=grade_id,
        received_on=receipt.doc_date,
    )


def _post_found(line: InventoryLine, user) -> None:
    fact = as_kg(line.fact_kg)
    if fact <= 0:
        raise WarehouseError("У новой паллеты вес должен быть больше нуля")
    if not line.product_id or not line.caliber_id or not line.grade_id:
        raise WarehouseError("У новой паллеты не указан товар, калибр или сорт")
    task, receipt = _opening_task(line.document, line.supplier, user)
    lot = _lot_on_receipt(receipt, line.product_id, line.caliber_id, line.grade_id)
    task_line = TaskLine.objects.create(
        task=task,
        product_id=line.product_id,
        caliber_id=line.caliber_id,
        grade_id=line.grade_id,
        qty_kg=fact,
    )
    now = timezone.now()
    pallet = Pallet.objects.create(
        kind="inbound",
        status="closed",
        location=line.location or "",
        created_by=user,
        closed_by=user,
        closed_at=now,
    )
    pallet.number = document_number("ПЛ", pallet.id)
    pallet.barcode = barcode("PL", pallet.id)
    pallet.save(update_fields=["number", "barcode"])
    post_in(task=task, task_line=task_line, pallet=pallet, lot=lot, qty_kg=fact, user=user)
    line.pallet = pallet
    line.lot = lot
    line.save(update_fields=["pallet", "lot"])


def _post_line(line: InventoryLine, user) -> None:
    if line.pallet_id is None:
        _post_found(line, user)
        return
    movement = (
        Movement.objects.select_for_update()
        .filter(pallet_id=line.pallet_id, kind=Movement.Kind.IN)
        .select_related("lot", "pallet")
        .first()
    )
    label = line.pallet.number or f"#{line.pallet_id}"
    if movement is None:
        raise WarehouseError(f"Нет прихода по паллете {label}")
    if has_downstream(movement):
        raise WarehouseError(f"По паллете {label} уже есть отбор или отгрузка")
    fact = as_kg(line.fact_kg)
    if fact == 0:
        remove_in(movement)
        return
    book = as_kg(line.book_kg)
    supplier_changed = line.supplier_id != line.lot.supplier_id
    if fact == book and not supplier_changed:
        return
    lot = movement.lot
    qty = fact if fact != book else as_kg(movement.qty_kg)
    if supplier_changed:
        lot = _lot_for_supplier(movement.lot, line.supplier_id)
    if lot.id == movement.lot_id and as_kg(movement.qty_kg) == qty:
        return
    update_in(movement, lot=lot, qty_kg=qty)


@transaction.atomic
def post_document(document: InventoryDocument, user) -> None:
    document = InventoryDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != InventoryDocument.Status.COUNTING:
        raise WarehouseError("Провести можно только документ в подсчёте")
    lines = list(document.lines.select_related("lot", "pallet", "supplier", "product", "caliber", "grade"))
    pending = sum(1 for line in lines if line.fact_kg is None)
    if pending:
        raise WarehouseError(f"Не посчитано паллет: {pending}")
    for line in lines:
        _post_line(line, user)
    document.status = InventoryDocument.Status.POSTED
    document.posted_by = user
    document.posted_at = timezone.now()
    document.save(update_fields=["status", "posted_by", "posted_at"])


@transaction.atomic
def cancel_document(document: InventoryDocument) -> None:
    document = InventoryDocument.objects.select_for_update().get(pk=document.pk)
    if document.status not in (InventoryDocument.Status.DRAFT, InventoryDocument.Status.COUNTING):
        raise WarehouseError("Проведённый документ нельзя отменить")
    document.status = InventoryDocument.Status.CANCELLED
    document.save(update_fields=["status"])
