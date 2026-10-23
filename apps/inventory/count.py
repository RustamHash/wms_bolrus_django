from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Caliber, Grade, Packaging, Product, Supplier
from apps.common.constants import PALLET_TARE
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row, parse_kg, require_int, text
from apps.common.kg import as_kg
from apps.common.numbers import barcode, document_number
from apps.inventory.models import InventoryDocument, InventoryLine, Movement
from apps.inventory.services import consumed_pallet_ids, has_left, post_in, remove_in, update_in
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


def scoped_products(document: InventoryDocument):
    if document.scope == InventoryDocument.Scope.PRODUCTS:
        return document.products.order_by("name")
    return Product.objects.order_by("name")


def read_measure(form) -> dict:
    from apps.receiving.services import _resolve_weight

    packaging = get_row(Packaging, require_int(form, "packaging_id", "Упаковка"), "тара")
    places = require_int(form, "places", "Количество упаковок")
    pallet_type = text(form, "pallet_type")
    manual = form.get("net_manual") == "1"
    gross_kg = parse_kg(form.get("gross"), "Грязный вес")
    net_kg = parse_kg(form.get("net"), "Чистый вес") if manual else None
    gross, net = _resolve_weight(places, pallet_type, gross_kg, net_kg, packaging.tare_kg, manual)
    return {
        "packaging": packaging,
        "places": places,
        "pallet_type": pallet_type,
        "gross_kg": gross,
        "net_kg": net,
    }


def _has_measure(line: InventoryLine) -> bool:
    return bool(line.packaging_id and line.places and line.pallet_type and line.gross_kg is not None)


def describe_measure(line: InventoryLine) -> dict:
    from apps.receiving.services import expected_net

    if _has_measure(line):
        packaging = line.packaging
        manual = False
        if packaging is not None and line.fact_kg is not None and line.pallet_type in PALLET_TARE:
            manual = as_kg(line.fact_kg) != expected_net(line.gross_kg, line.places, line.pallet_type, packaging.tare_kg)
        return {
            "pallet_type": line.pallet_type,
            "packaging_id": line.packaging_id,
            "packaging_name": packaging.name if packaging is not None else "",
            "places": line.places,
            "gross": line.gross_kg,
            "net": line.fact_kg,
            "manual": manual,
        }
    pallet = line.pallet if line.pallet_id else None
    if pallet is None:
        return {}
    units = list(pallet.units.all())
    if not units:
        return {}
    packaging_ids = {unit.packaging_id for unit in units}
    packaging = units[0].packaging if len(packaging_ids) == 1 else None
    gross = sum((as_kg(unit.gross_kg) for unit in units), Decimal("0.000"))
    net = sum((as_kg(unit.net_kg) for unit in units), Decimal("0.000"))
    pallet_type = pallet.pallet_type or ""
    manual = False
    if packaging is not None and pallet_type in PALLET_TARE:
        manual = net != expected_net(gross, len(units), pallet_type, packaging.tare_kg)
    if line.fact_kg is not None and as_kg(line.fact_kg) > 0 and as_kg(line.fact_kg) != net:
        net = as_kg(line.fact_kg)
        manual = True
    return {
        "pallet_type": pallet_type,
        "packaging_id": packaging.id if packaging is not None else "",
        "packaging_name": packaging.name if packaging is not None else "разные",
        "places": len(units),
        "gross": gross,
        "net": net,
        "manual": manual,
    }


def with_measure(rows):
    for row in rows:
        row.measure = describe_measure(row)
        row.saved_measure = _has_measure(row)
    return rows


def stock_inbounds(product_ids: list[int] | None = None):
    """Приходы паллет, которые ещё на складе: без отбора, отгрузки и переработки."""
    rows = Movement.objects.filter(kind=Movement.Kind.IN).exclude(pallet_id__in=consumed_pallet_ids())
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
def save_count(
    line: InventoryLine,
    *,
    fact_kg: Decimal | None,
    supplier_id: int,
    user,
    fact_given: bool,
    measure: dict | None = None,
) -> InventoryLine:
    line = InventoryLine.objects.select_for_update().select_related("document").get(pk=line.pk)
    _open_line(line)
    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if supplier is None:
        raise WarehouseError("Поставщик не найден")
    line.supplier = supplier
    fields = ["supplier"]
    if measure is not None:
        fact = as_kg(measure["net_kg"])
        if line.pallet_id is None and fact <= 0:
            raise WarehouseError("У новой паллеты вес должен быть больше нуля")
        line.packaging = measure["packaging"]
        line.places = measure["places"]
        line.pallet_type = measure["pallet_type"]
        line.gross_kg = measure["gross_kg"]
        line.fact_kg = fact
        line.counted_by = user
        line.counted_at = timezone.now()
        fields += ["packaging", "places", "pallet_type", "gross_kg", "fact_kg", "counted_by", "counted_at"]
    elif fact_given:
        fact = as_kg(fact_kg)
        if line.pallet_id is None and fact <= 0:
            raise WarehouseError("У новой паллеты вес должен быть больше нуля")
        if fact == 0 or (line.gross_kg is not None and (line.fact_kg is None or as_kg(line.fact_kg) != fact)):
            line.packaging = None
            line.places = None
            line.pallet_type = ""
            line.gross_kg = None
            fields += ["packaging", "places", "pallet_type", "gross_kg"]
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
    cell,
    measure: dict,
    user,
) -> InventoryLine:
    document = InventoryDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != InventoryDocument.Status.COUNTING:
        raise WarehouseError("Добавлять паллету можно только во время подсчёта")
    qty = as_kg(measure["net_kg"])
    if qty <= 0:
        raise WarehouseError("Укажите вес больше нуля")
    product = get_row(Product, product_id, "товар")
    caliber = get_row(Caliber, caliber_id, "калибр")
    grade = get_row(Grade, grade_id, "сорт")
    supplier = get_row(Supplier, supplier_id, "поставщик")
    if document.scope == InventoryDocument.Scope.PRODUCTS and not document.products.filter(pk=product.id).exists():
        raise WarehouseError("Этот товар не входит в инвентаризацию")
    if cell is None:
        raise WarehouseError("Укажите ячейку существующей зоны")
    return InventoryLine.objects.create(
        document=document,
        product=product,
        caliber=caliber,
        grade=grade,
        supplier=supplier,
        cell=cell,
        location=cell.label(),
        packaging=measure["packaging"],
        pallet_type=measure["pallet_type"],
        places=measure["places"],
        gross_kg=measure["gross_kg"],
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
            receipt_id=source.receipt_id,
            source=source.source,
            parent_id=source.parent_id,
            is_opening=source.is_opening,
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
        source=source.source,
        parent_id=source.parent_id,
        expires_on=source.expires_on,
        passport_grade=source.passport_grade,
        passport_supplier=source.passport_supplier,
        is_opening=source.is_opening,
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
        receipt=receipt,
        product_id=product_id,
        caliber_id=caliber_id,
        grade_id=grade_id,
        source=Lot.Source.INVENTORY,
        parent__isnull=True,
        is_opening=True,
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
        source=Lot.Source.INVENTORY,
        is_opening=True,
    )


def _apply_measure(line: InventoryLine, pallet: Pallet, lot) -> None:
    from apps.receiving.services import replace_units

    if pallet.kind == "loose":
        pallet.kind = "inbound"
        pallet.save(update_fields=["kind"])
    replace_units(
        pallet=pallet,
        receipt=lot.receipt,
        lot=lot,
        packaging=line.packaging,
        places=line.places,
        pallet_type=line.pallet_type,
        gross_kg=as_kg(line.gross_kg),
        net_kg=as_kg(line.fact_kg),
        grade_id=lot.grade_id,
    )


def _needs_units(line: InventoryLine, pallet: Pallet, lot) -> bool:
    if not _has_measure(line):
        return False
    units = list(pallet.units.all())
    if len(units) != line.places or (pallet.pallet_type or "") != line.pallet_type:
        return True
    gross = Decimal("0.000")
    net = Decimal("0.000")
    for unit in units:
        if unit.packaging_id != line.packaging_id or unit.lot_id != lot.id or unit.grade_id != lot.grade_id:
            return True
        gross += as_kg(unit.gross_kg)
        net += as_kg(unit.net_kg)
    return gross != as_kg(line.gross_kg) or net != as_kg(line.fact_kg)


def _post_found(line: InventoryLine, user) -> None:
    fact = as_kg(line.fact_kg)
    if fact <= 0:
        raise WarehouseError("У новой паллеты вес должен быть больше нуля")
    if not line.product_id or not line.caliber_id or not line.grade_id:
        raise WarehouseError("У новой паллеты не указан товар, калибр или сорт")
    if not _has_measure(line):
        raise WarehouseError("Укажите вид паллета, упаковку, число мест и грязный вес — как при приёмке")
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
        pallet_type=line.pallet_type,
        cell=line.cell,
        location=line.place,
        created_by=user,
        closed_by=user,
        closed_at=now,
    )
    pallet.number = document_number("ПЛ", pallet.id)
    pallet.barcode = barcode("PL", pallet.id)
    pallet.save(update_fields=["number", "barcode"])
    _apply_measure(line, pallet, lot)
    post_in(task=task, task_line=task_line, pallet=pallet, lot=lot, qty_kg=fact, user=user)
    line.pallet = pallet
    line.lot = lot
    line.save(update_fields=["pallet", "lot"])
    from apps.printing.services import schedule_accepted_passport

    schedule_accepted_passport(pallet.id, getattr(user, "id", None))


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
    if has_left(movement):
        raise WarehouseError(f"По паллете {label} уже есть отбор, отгрузка или переработка")
    fact = as_kg(line.fact_kg)
    if fact == 0:
        remove_in(movement)
        return
    supplier_changed = line.supplier_id != line.lot.supplier_id
    lot = _lot_for_supplier(movement.lot, line.supplier_id) if supplier_changed else movement.lot
    if _needs_units(line, movement.pallet, lot):
        _apply_measure(line, movement.pallet, lot)
    if lot.id == movement.lot_id and as_kg(movement.qty_kg) == fact:
        return
    update_in(movement, lot=lot, qty_kg=fact)


@transaction.atomic
def post_document(document: InventoryDocument, user) -> None:
    document = InventoryDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != InventoryDocument.Status.COUNTING:
        raise WarehouseError("Провести можно только документ в подсчёте")
    lines = list(
        document.lines.select_related(
            "lot", "lot__receipt", "pallet", "supplier", "product", "caliber", "grade", "packaging", "cell__zone"
        ).prefetch_related("pallet__units")
    )
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
