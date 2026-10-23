from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import Caliber, Grade, Product
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.kg import as_kg, format_kg
from apps.common.numbers import barcode, document_number
from apps.inventory.models import InventoryDocument, InventoryLine, Movement, TransformDocument, TransformInput, TransformOutput, TransformWaste
from apps.inventory.services import (
    consumed_pallet_ids,
    has_left,
    inbound_is_reserved,
    lock_balances,
    post_transform_in,
    post_transform_out,
    post_waste,
)
from apps.receiving.models import Lot
from apps.shipping.models import Allocation
from apps.storage.models import Pallet

_PREFIX = {
    TransformDocument.Kind.REWORK: "ПБ",
    TransformDocument.Kind.WORKSHOP: "ЦХ",
}


def parse_tolerance(raw: str) -> Decimal:
    value_text = (raw or "").strip().replace(",", ".")
    if not value_text:
        return Decimal("0.000")
    try:
        value = Decimal(value_text)
    except InvalidOperation as exc:
        raise WarehouseError("Некорректный допуск") from exc
    value = value.quantize(Decimal("0.001"))
    if value < 0:
        raise WarehouseError("Допуск не может быть отрицательным")
    return value


def _totals(document: TransformDocument) -> tuple[Decimal, Decimal, Decimal]:
    incoming = document.inputs.aggregate(total=Sum("qty_kg"))["total"]
    outgoing = document.outputs.aggregate(total=Sum("qty_kg"))["total"]
    waste = document.wastes.aggregate(total=Sum("qty_kg"))["total"]
    return as_kg(incoming or 0), as_kg(outgoing or 0), as_kg(waste or 0)


def mass_state(document: TransformDocument) -> dict:
    incoming, outgoing, waste = _totals(document)
    gap = as_kg(incoming - outgoing - waste)
    tolerance = as_kg(document.tolerance_kg)
    return {
        "input_kg": incoming,
        "output_kg": outgoing,
        "waste_kg": waste,
        "gap_kg": gap,
        "tolerance_kg": tolerance,
        "fits": abs(gap) <= tolerance and incoming > 0,
    }


def _draft(document: TransformDocument) -> TransformDocument:
    document = TransformDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != TransformDocument.Status.DRAFT:
        raise WarehouseError("Менять можно только черновик")
    return document


def _stock_inbound(pallet_id: int):
    inbound = (
        Movement.objects.select_for_update()
        .select_related("lot", "pallet")
        .filter(pallet_id=pallet_id, kind=Movement.Kind.IN)
        .first()
    )
    if inbound is None:
        raise WarehouseError("Паллета не принята")
    if has_left(inbound):
        raise WarehouseError("Паллета уже отобрана, отгружена или списана")
    return inbound


@transaction.atomic
def create_document(*, kind: str, doc_date, tolerance_kg: Decimal, notes: str, user) -> TransformDocument:
    if kind not in TransformDocument.Kind.values:
        raise WarehouseError("Укажите переборку или цех")
    tolerance = as_kg(tolerance_kg)
    if tolerance < 0:
        raise WarehouseError("Допуск не может быть отрицательным")
    document = TransformDocument.objects.create(
        kind=kind,
        doc_date=doc_date,
        tolerance_kg=tolerance,
        notes=(notes or "").strip(),
        created_by=user,
    )
    document.number = document_number(_PREFIX[kind], document.id)
    document.save(update_fields=["number"])
    return document


@transaction.atomic
def save_header(
    document: TransformDocument,
    *,
    doc_date,
    tolerance_kg: Decimal,
    notes: str,
    category_ids: list[int] | None = None,
    output_consignee_id: int | None = None,
    output_line_id: int | None = None,
) -> TransformDocument:
    document = _draft(document)
    tolerance = as_kg(tolerance_kg)
    if tolerance < 0:
        raise WarehouseError("Допуск не может быть отрицательным")
    if output_consignee_id and output_line_id:
        raise WarehouseError("Укажите либо строку заказа, либо клиента")
    document.doc_date = doc_date
    document.tolerance_kg = tolerance
    document.notes = (notes or "").strip()
    document.categories_set = True
    document.output_consignee_id = output_consignee_id or None
    document.output_line_id = output_line_id or None
    document.save(update_fields=["doc_date", "tolerance_kg", "notes", "categories_set", "output_consignee", "output_line"])
    document.allowed_categories.set(_known_categories(category_ids))
    _output_reserve_fits(document)
    return document


def _known_categories(raw: list[int] | None) -> list[int]:
    from apps.catalog.models import ConsigneeCategory

    if not raw:
        return []
    return list(ConsigneeCategory.objects.filter(pk__in=raw).values_list("id", flat=True))


def suggest_categories(document: TransformDocument) -> set[int] | None:
    sets = []
    inputs = document.inputs.select_related("pallet").prefetch_related("pallet__allowed_categories")
    for line in inputs:
        found = {item.id for item in line.pallet.allowed_categories.all()}
        if found:
            sets.append(found)
    if not sets:
        return set()
    shared = set.intersection(*sets)
    if not shared:
        return None
    return shared


def output_category_ids(document: TransformDocument) -> list[int]:
    if document.categories_set:
        return list(document.allowed_categories.values_list("id", flat=True))
    suggested = suggest_categories(document)
    if suggested is None:
        raise WarehouseError("У входных паллет нет общей категории. Отметьте категории выхода")
    return sorted(suggested)


def _output_reserve_fits(document: TransformDocument) -> None:
    if document.output_line_id and document.output_consignee_id:
        raise WarehouseError("Укажите либо строку заказа, либо клиента")
    if not document.output_line_id:
        return
    line = document.output_line
    consignee = line.order.consignee if line.order_id else None
    allowed = set(output_category_ids(document))
    if allowed and (consignee is None or consignee.category_id not in allowed):
        raise WarehouseError("Заказ этой категории не может взять паллету")


def stock_choices(document: TransformDocument) -> list[dict]:
    used = set(document.inputs.values_list("pallet_id", flat=True))
    busy = TransformInput.objects.filter(document__status=TransformDocument.Status.DRAFT).values("pallet_id")
    rows = (
        Movement.objects.filter(kind=Movement.Kind.IN)
        .exclude(pallet_id__in=consumed_pallet_ids())
        .exclude(pallet_id__in=busy)
        .select_related("pallet", "lot__product", "lot__caliber", "lot__grade")
        .order_by("lot__product__name", "pallet__number", "id")
    )
    choices = []
    for movement in rows:
        if movement.pallet_id in used:
            continue
        pallet = movement.pallet
        lot = movement.lot
        label = (
            f"{pallet.number or pallet.id} · {lot.product.name} · {lot.caliber.name} · "
            f"{lot.grade.name} · {format_kg(movement.qty_kg)} кг"
        )
        if pallet.order_id or pallet.reserved_consignee_id or pallet.reserved_line_id:
            label += " · резерв"
        choices.append({"id": pallet.id, "label": label})
    return choices


@transaction.atomic
def add_input(document: TransformDocument, pallet_id: int) -> TransformInput:
    document = _draft(document)
    inbound = _stock_inbound(pallet_id)
    if TransformInput.objects.filter(document=document, pallet_id=pallet_id).exists():
        raise WarehouseError("Эта паллета уже в документе")
    if (
        TransformInput.objects.filter(pallet_id=pallet_id, document__status=TransformDocument.Status.DRAFT)
        .exclude(document=document)
        .exists()
    ):
        raise WarehouseError("Паллета уже стоит в другом черновике")
    if InventoryLine.objects.filter(
        pallet_id=pallet_id, document__status=InventoryDocument.Status.COUNTING
    ).exists():
        raise WarehouseError("Паллета в незакрытой инвентаризации")
    return TransformInput.objects.create(
        document=document,
        pallet_id=inbound.pallet_id,
        lot_id=inbound.lot_id,
        qty_kg=as_kg(inbound.qty_kg),
    )


@transaction.atomic
def add_output(
    document: TransformDocument,
    *,
    product_id: int,
    caliber_id: int,
    grade_id: int,
    qty_kg: Decimal,
    expires_on,
) -> TransformOutput:
    document = _draft(document)
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес выхода должен быть больше нуля")
    product = get_row(Product, product_id, "товар")
    caliber = get_row(Caliber, caliber_id, "калибр")
    grade = get_row(Grade, grade_id, "сорт")
    return TransformOutput.objects.create(
        document=document,
        product=product,
        caliber=caliber,
        grade=grade,
        qty_kg=qty,
        expires_on=expires_on,
    )


@transaction.atomic
def add_waste(document: TransformDocument, *, qty_kg: Decimal, reason: str) -> TransformWaste:
    document = _draft(document)
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес отхода должен быть больше нуля")
    text = (reason or "").strip()
    if len(text) > 200:
        raise WarehouseError("Причина длиннее 200 символов")
    return TransformWaste.objects.create(document=document, qty_kg=qty, reason=text)


def _delete_line(document: TransformDocument, model, line_id: int) -> None:
    document = _draft(document)
    line = model.objects.filter(pk=line_id, document=document).first()
    if line is None:
        raise WarehouseError("Строка не найдена")
    line.delete()


@transaction.atomic
def delete_input(document: TransformDocument, line_id: int) -> None:
    _delete_line(document, TransformInput, line_id)


@transaction.atomic
def delete_output(document: TransformDocument, line_id: int) -> None:
    _delete_line(document, TransformOutput, line_id)


@transaction.atomic
def delete_waste(document: TransformDocument, line_id: int) -> None:
    _delete_line(document, TransformWaste, line_id)


@transaction.atomic
def cancel_document(document: TransformDocument) -> None:
    document = TransformDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != TransformDocument.Status.DRAFT:
        raise WarehouseError("Проведённый документ нельзя отменить")
    document.status = TransformDocument.Status.CANCELLED
    document.save(update_fields=["status"])


def _parent_lot(inbounds: list) -> Lot:
    lots = [inbound.lot for _line, inbound in inbounds]
    return min(lots, key=lambda lot: (lot.received_on, lot.id))


def _split(inbounds: list, output_kg: Decimal) -> list[tuple]:
    remaining = as_kg(output_kg)
    rows = []
    for line, inbound in inbounds:
        qty = as_kg(inbound.qty_kg)
        out_part = as_kg(min(qty, remaining))
        remaining = as_kg(remaining - out_part)
        rows.append((line, inbound, out_part, as_kg(qty - out_part)))
    return rows


def _clear_pallet(pallet: Pallet) -> None:
    unit_ids = list(pallet.units.values_list("id", flat=True))
    if unit_ids:
        Allocation.objects.filter(unit_id__in=unit_ids).delete()
    pallet.units.exclude(status="rejected").update(status="transformed")
    if pallet.order_id or pallet.reserved_consignee_id or pallet.reserved_line_id:
        pallet.order = None
        pallet.reserved_consignee = None
        pallet.reserved_line = None
        pallet.save(update_fields=["order", "reserved_consignee", "reserved_line"])


def _output_pallet(user) -> Pallet:
    now = timezone.now()
    pallet = Pallet.objects.create(
        kind="inbound",
        status="closed",
        created_by=user,
        closed_by=user,
        closed_at=now,
    )
    pallet.number = document_number("ПЛ", pallet.id)
    pallet.barcode = barcode("PL", pallet.id)
    pallet.save(update_fields=["number", "barcode"])
    return pallet


@transaction.atomic
def post_document(document: TransformDocument, user) -> str:
    document = (
        TransformDocument.objects.select_for_update(of=("self",))
        .select_related("output_line__order__consignee", "output_consignee")
        .get(pk=document.pk)
    )
    if document.status != TransformDocument.Status.DRAFT:
        raise WarehouseError("Провести можно только черновик")
    if document.kind not in (Lot.Source.REWORK, Lot.Source.WORKSHOP):
        raise WarehouseError("Укажите переборку или цех")
    inputs = list(document.inputs.select_related("pallet", "lot").order_by("id"))
    outputs = list(document.outputs.select_related("product", "caliber", "grade").order_by("id"))
    if not inputs:
        raise WarehouseError("Добавьте паллету на вход")
    locked = []
    for line in inputs:
        inbound = _stock_inbound(line.pallet_id)
        label = line.pallet.number or f"#{line.pallet_id}"
        if as_kg(inbound.qty_kg) != as_kg(line.qty_kg) or inbound.lot_id != line.lot_id:
            raise WarehouseError(f"Паллета {label} изменилась. Уберите её и добавьте снова")
        if InventoryLine.objects.filter(
            pallet_id=line.pallet_id, document__status=InventoryDocument.Status.COUNTING
        ).exists():
            raise WarehouseError(f"Паллета {label} в незакрытой инвентаризации")
        locked.append((line, inbound))
    incoming, outgoing, declared = _totals(document)
    gap = as_kg(incoming - outgoing - declared)
    tolerance = as_kg(document.tolerance_kg)
    if abs(gap) > tolerance:
        raise WarehouseError(
            "Баланс масс не сходится: "
            f"вход {format_kg(incoming)}, выход {format_kg(outgoing)}, отход {format_kg(declared)}, "
            f"расхождение {format_kg(gap)} при допуске {format_kg(tolerance)}"
        )
    lock_balances(inbound.lot_id for _line, inbound in locked)
    category_ids = output_category_ids(document)
    _output_reserve_fits(document)
    output_reserved = bool(document.output_line_id or document.output_consignee_id)
    parent = _parent_lot(locked)
    for line, inbound, out_part, waste_part in _split(locked, outgoing):
        reserved = inbound_is_reserved(inbound)
        if out_part > 0:
            post_transform_out(
                pallet=inbound.pallet,
                lot=inbound.lot,
                qty_kg=out_part,
                user=user,
                document=document,
                reserved=reserved,
                source=inbound,
            )
        if waste_part > 0:
            post_waste(
                pallet=inbound.pallet,
                lot=inbound.lot,
                qty_kg=waste_part,
                user=user,
                document=document,
                reserved=reserved,
                source=inbound,
            )
        _clear_pallet(inbound.pallet)
    for line in outputs:
        lot = Lot.objects.create(
            receipt_id=parent.receipt_id,
            parent=parent,
            source=document.kind,
            supplier_id=parent.supplier_id,
            product_id=line.product_id,
            caliber_id=line.caliber_id,
            grade_id=line.grade_id,
            received_on=document.doc_date,
            expires_on=line.expires_on or parent.expires_on,
            is_opening=False,
        )
        pallet = _output_pallet(user)
        if category_ids:
            pallet.allowed_categories.set(category_ids)
        if document.output_line_id:
            pallet.reserved_line_id = document.output_line_id
            pallet.order_id = document.output_line.order_id
        elif document.output_consignee_id:
            pallet.reserved_consignee_id = document.output_consignee_id
        if output_reserved:
            pallet.save(update_fields=["reserved_line", "order", "reserved_consignee"])
        post_transform_in(
            pallet=pallet,
            lot=lot,
            qty_kg=line.qty_kg,
            user=user,
            document=document,
            reserved=output_reserved,
        )
        line.lot = lot
        line.pallet = pallet
        line.save(update_fields=["lot", "pallet"])
    book = as_kg(incoming - outgoing) if incoming >= outgoing else Decimal("0.000")
    document.status = TransformDocument.Status.POSTED
    document.ledger_waste_kg = book
    document.posted_by = user
    document.posted_at = timezone.now()
    document.save(update_fields=["status", "ledger_waste_kg", "posted_by", "posted_at"])
    message = "Документ проведён"
    if outgoing > incoming:
        message += f". Выход больше входа на {format_kg(outgoing - incoming)} кг, в допуске"
    elif declared != book:
        message += f". Отход в движениях {format_kg(book)} кг, в строках {format_kg(declared)} кг"
    return message
