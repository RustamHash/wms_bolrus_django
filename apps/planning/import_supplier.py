from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.catalog.models import ExternalProduct, ProductAlias
from apps.catalog.parties import upsert_external, upsert_supplier
from apps.common.exceptions import WarehouseError
from apps.planning.import_customer import (
    ORDERS_DIR,
    ImportReport,
    _cell,
    _columns,
    _detail_rows,
    _order_key,
    _parse_date,
    is_feedback_name,
)
from apps.planning.models import PurchaseOrder, PurchaseOrderLine
from apps.planning.purchase_revisions import apply_purchase_import, store_baseline
from apps.planning.services import use_purchase_number
from apps.planning.revisions import revision_summary

HEADER = ("Дата поступления", "Номер", "Состояние", "Поставщик")
STATES = {
    "Отправка на склад": "incoming",
    "Завершен": "closed",
}


def _supplier_files(key: int) -> list:
    token = f"№ НФ-{key} от"
    if not ORDERS_DIR.is_dir():
        return []
    return sorted(path for path in ORDERS_DIR.glob("Заказ поставщику*.xlsx") if token in path.name)


def _row(order: PurchaseOrder, external: str, party: str, changes: dict | None = None) -> dict:
    return {
        "id": order.id,
        "number": order.number,
        "external": external,
        "party": party,
        "lines": order.lines.count(),
        "summary": revision_summary(changes or {}),
        "changes": changes or {},
    }


def _lines(path, external: str, externals: dict, aliases: dict, report: ImportReport) -> list[dict]:
    lines = []
    for item in _detail_rows(path):
        feedback = is_feedback_name(item["name"])
        if item["unit"] != "кг" and not feedback:
            report.skipped_units.append({"order": external, "name": item["name"], "unit": item["unit"] or "—"})
            continue
        try:
            qty = Decimal(item["qty"]).quantize(Decimal("0.001"))
        except InvalidOperation:
            report.missing_codes.append(
                {"order": external, "code": item["code"], "name": item["name"] or "некорректное количество"}
            )
            continue
        if qty <= 0:
            continue
        card = externals.get(item["code"])
        if card is None:
            card = upsert_external(item["code"], item["name"])
            externals[item["code"]] = card
        ours = None if feedback else aliases.get(item["code"])
        lines.append(
            {
                "product_id": ours.id if ours is not None else None,
                "external_product_id": card.id,
                "qty_kg": qty,
                "external_code": item["code"],
                "external_name": item["name"],
                "unit": item["unit"] or "кг",
                "for_task": not feedback,
            }
        )
    return lines


def _store_lines(order: PurchaseOrder, lines: list[dict]) -> None:
    for item in lines:
        PurchaseOrderLine.objects.create(
            order=order,
            product_id=item["product_id"],
            external_product_id=item["external_product_id"],
            qty_kg=item["qty_kg"],
            external_code=item["external_code"],
            external_name=item["external_name"],
            unit=item["unit"],
            for_task=item["for_task"],
        )


@transaction.atomic
def import_supplier_orders(source, user, update: bool = False) -> ImportReport:
    from apps.common.xlsx import read_rows

    rows = read_rows(source)
    columns = _columns(rows.get(1) or {})
    missing = [name for name in HEADER if name not in columns]
    if missing:
        raise WarehouseError("В шапке нет колонок: " + ", ".join(missing))
    externals = {item.code: item for item in ExternalProduct.objects.all()}
    aliases = {
        item.external_product.code: item.product
        for item in ProductAlias.objects.select_related("external_product", "product")
    }
    report = ImportReport()
    for number in sorted(row for row in rows if row != 1):
        cells = rows[number]
        external = _cell(cells, columns, "Номер")
        if not external:
            continue
        state = _cell(cells, columns, "Состояние")
        status = STATES.get(state)
        if status is None:
            report.other_status += 1
            continue
        found = (
            PurchaseOrder.objects.filter(external_number=external).select_related("supplier").first()
            or PurchaseOrder.objects.filter(number=external).select_related("supplier").first()
        )
        if found is not None and not update:
            report.duplicates.append(external)
            continue
        key = _order_key(external)
        files = _supplier_files(key) if key is not None else []
        if len(files) != 1:
            reason = "несколько файлов" if len(files) > 1 else "нет файла"
            report.missing_files.append(f"{external}: {reason}")
            continue
        receipt = _parse_date(_cell(cells, columns, "Дата поступления"))
        if receipt is None:
            raise WarehouseError(f"{external}: не разобрана дата поступления")
        party = _cell(cells, columns, "Поставщик")
        if not party:
            raise WarehouseError(f"{external}: не указан поставщик")
        supplier = upsert_supplier(party, _cell(cells, columns, "КодПоставщик"))
        comment = _cell(cells, columns, "Комментарий")
        lines = _lines(files[0], external, externals, aliases, report)
        if not lines:
            report.empty.append(external)
            continue
        if found is None:
            if PurchaseOrder.objects.filter(number=external).exists():
                raise WarehouseError(f"{external}: заказ с таким номером уже есть")
            order = PurchaseOrder.objects.create(
                number=external,
                external_number=external,
                supplier=supplier,
                doc_date=receipt,
                receipt_date=receipt,
                status=status,
                comment=comment,
                created_by=user,
            )
            _store_lines(order, lines)
            store_baseline(order, user)
            report.created.append(_row(order, external, supplier.name))
            continue
        use_purchase_number(found, external)
        result = apply_purchase_import(
            found,
            user,
            supplier=supplier,
            doc_date=receipt,
            receipt_date=receipt,
            status=status,
            comment=comment,
            lines=lines,
        )
        row = _row(found, external, supplier.name, result["changes"])
        kind = result["kind"]
        if kind == "updated":
            row["revision"] = result["revision"]
            report.updated.append(row)
        elif kind == "closed":
            report.closed.append(row)
        elif kind == "blocked":
            report.blocked.append(row)
        else:
            report.unchanged.append(row)
    return report
