from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction

from apps.catalog.models import ExternalProduct, ProductAlias, Seller
from apps.catalog.parties import upsert_consignee, upsert_external, upsert_supplier
from apps.common.numbers import document_number
from apps.onec.dto import OrderDTO
from apps.onec.orders import OnecOrders
from apps.planning.import_customer import ImportReport, is_feedback_name
from apps.planning.models import Order, OrderLine, PurchaseOrder, PurchaseOrderLine
from apps.planning.purchase_revisions import apply_purchase_import, store_baseline as store_purchase_baseline
from apps.planning.revisions import apply_import, revision_summary, store_baseline
from apps.planning.services import create_order

PURCHASE_STATES = {
    "Отправка на склад": "incoming",
    "Завершен": "closed",
}
CUSTOMER_STATES = {"Отправка на склад"}
SELLER_ALIASES = (
    ("большая русь", "болрус"),
    ("болрус", "болрус"),
    ("байчора", "байчора"),
)

LogFn = Callable[[str], None]


def _emit(report: ImportReport, message: str, on_log: LogFn | None = None) -> str:
    report.log.append(message)
    if on_log:
        on_log(message)
    return message


def sync_purchase_orders(
    user,
    *,
    update: bool = False,
    top: int = 2000,
    date_from: date | None = None,
    date_to: date | None = None,
    number: str | None = None,
    api: OnecOrders | None = None,
    on_log: LogFn | None = None,
) -> ImportReport:
    report = None
    for item in iter_sync_purchase_orders(
        user,
        update=update,
        top=top,
        date_from=date_from,
        date_to=date_to,
        number=number,
        api=api,
        on_log=on_log,
    ):
        if isinstance(item, ImportReport):
            report = item
    return report or ImportReport()


def sync_customer_orders(
    user,
    *,
    update: bool = False,
    top: int = 2000,
    date_from: date | None = None,
    date_to: date | None = None,
    number: str | None = None,
    api: OnecOrders | None = None,
    on_log: LogFn | None = None,
) -> ImportReport:
    report = None
    for item in iter_sync_customer_orders(
        user,
        update=update,
        top=top,
        date_from=date_from,
        date_to=date_to,
        number=number,
        api=api,
        on_log=on_log,
    ):
        if isinstance(item, ImportReport):
            report = item
    return report or ImportReport()


def _filter_new_headers(
    headers: list[OrderDTO],
    *,
    existing: set[str],
    report: ImportReport,
) -> list[OrderDTO]:
    """Оставляет только заказы, которых ещё нет в WMS; дубли пишет в report."""
    fresh = []
    for doc in headers:
        external = (doc.number or "").strip()
        if external and external in existing:
            report.duplicates.append(external)
            continue
        fresh.append(doc)
    return fresh


def _existing_purchase_numbers(numbers: list[str]) -> set[str]:
    cleaned = [item for item in numbers if item]
    if not cleaned:
        return set()
    return set(
        PurchaseOrder.objects.filter(external_number__in=cleaned).values_list("external_number", flat=True)
    )


def _existing_customer_numbers(numbers: list[str]) -> set[str]:
    cleaned = [item for item in numbers if item]
    if not cleaned:
        return set()
    return set(Order.objects.filter(external_number__in=cleaned).values_list("external_number", flat=True))


def iter_sync_purchase_orders(
    user,
    *,
    update: bool = False,
    top: int = 2000,
    date_from: date | None = None,
    date_to: date | None = None,
    number: str | None = None,
    api: OnecOrders | None = None,
    on_log: LogFn | None = None,
) -> Iterator[str | ImportReport]:
    """Yields log lines, then ImportReport. В 1С ничего не пишет."""
    report = ImportReport()
    number = (number or "").strip() or None
    by_number = bool(number)
    # Обновление: галочка «Обновить» или импорт по номеру.
    allow_update = update or by_number
    if by_number:
        yield _emit(report, f"Импорт одного заказа по номеру: {number}", on_log)
    else:
        date_from, date_to = delivery_period(date_from, date_to)
        yield _emit(report, f"Период поступления: {date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}", on_log)
    if allow_update:
        mode = "новые и обновление" if update else "по номеру (с обновлением)"
    else:
        mode = "только новые (уже есть в WMS — пропуск)"
    yield _emit(report, f"Режим: {mode}", on_log)
    yield _emit(report, "Подключение к 1С…", on_log)
    try:
        api = api or OnecOrders()
        if by_number:
            found = api.find_purchase_by_number(number, with_lines=False, resolve=False)
            headers = [found] if found else []
        else:
            yield _emit(
                report,
                f"Запрос шапок заказов поставщику за период (1 запрос OData, лимит {top})…",
                on_log,
            )
            headers = api.list_purchase_orders(
                top=top, date_from=date_from, date_to=date_to, with_lines=False, resolve=False
            )
        yield _emit(report, "Подключение к 1С — успешно", on_log)
    except Exception as exc:
        yield _emit(report, f"Подключение к 1С — ошибка: {exc}", on_log)
        yield report
        raise
    if by_number and not headers:
        yield _emit(report, f"Заказ «{number}» в 1С не найден", on_log)
        yield _emit(report, "Импорт завершён", on_log)
        yield report
        return
    yield _emit(report, f"Шапок получено: {len(headers)}", on_log)
    if not by_number and len(headers) >= top:
        yield _emit(report, f"Внимание: достигнут лимит {top}, в 1С могут быть ещё заказы", on_log)
    if by_number:
        candidates = headers
        skipped = 0
    else:
        candidates = [doc for doc in headers if PURCHASE_STATES.get(doc.state)]
        skipped = len(headers) - len(candidates)
    report.other_status = skipped
    yield _emit(report, f"К импорту по статусу: {len(candidates)}", on_log)
    if skipped:
        yield _emit(report, f"Пропущено по статусу: {skipped}", on_log)
    if not allow_update and candidates:
        existing = _existing_purchase_numbers([(doc.number or "").strip() for doc in candidates])
        before = len(candidates)
        candidates = _filter_new_headers(candidates, existing=existing, report=report)
        skipped_existing = before - len(candidates)
        if skipped_existing:
            yield _emit(report, f"Уже есть в WMS, пропущено: {skipped_existing}", on_log)
        yield _emit(report, f"К загрузке строк (только новые): {len(candidates)}", on_log)
    externals, aliases = _catalogs()
    total = len(candidates)
    for index, header in enumerate(candidates, start=1):
        label = header.number or header.ref_key or "?"
        yield _emit(report, f"Строки и справочники {label} — {index} из {total}…", on_log)
        try:
            doc = api.expand_purchase(header)
        except Exception as exc:
            yield _emit(report, f"Ошибка загрузки {label}: {exc} — {index} из {total}", on_log)
            continue
        message = _sync_purchase(
            doc,
            user,
            update=allow_update,
            externals=externals,
            aliases=aliases,
            report=report,
            any_status=by_number,
        )
        if message:
            yield _emit(report, f"{message} — {index} из {total}", on_log)
    yield _emit(report, "Импорт завершён", on_log)
    yield report


def iter_sync_customer_orders(
    user,
    *,
    update: bool = False,
    top: int = 2000,
    date_from: date | None = None,
    date_to: date | None = None,
    number: str | None = None,
    api: OnecOrders | None = None,
    on_log: LogFn | None = None,
) -> Iterator[str | ImportReport]:
    """Yields log lines, then ImportReport. В 1С ничего не пишет."""
    report = ImportReport()
    number = (number or "").strip() or None
    by_number = bool(number)
    # Обновление: галочка «Обновить» или импорт по номеру.
    allow_update = update or by_number
    if by_number:
        yield _emit(report, f"Импорт одного заказа по номеру: {number}", on_log)
    else:
        date_from, date_to = delivery_period(date_from, date_to)
        yield _emit(report, f"Период отгрузки: {date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}", on_log)
    if allow_update:
        mode = "новые и обновление" if update else "по номеру (с обновлением)"
    else:
        mode = "только новые (уже есть в WMS — пропуск)"
    yield _emit(report, f"Режим: {mode}", on_log)
    yield _emit(report, "Подключение к 1С…", on_log)
    try:
        api = api or OnecOrders()
        if by_number:
            found = api.find_customer_by_number(number, with_lines=False, resolve=False)
            headers = [found] if found else []
        else:
            yield _emit(
                report,
                f"Запрос шапок заказов покупателей за период (1 запрос OData, лимит {top})…",
                on_log,
            )
            headers = api.list_customer_orders(
                top=top, date_from=date_from, date_to=date_to, with_lines=False, resolve=False
            )
        yield _emit(report, "Подключение к 1С — успешно", on_log)
    except Exception as exc:
        yield _emit(report, f"Подключение к 1С — ошибка: {exc}", on_log)
        yield report
        raise
    if by_number and not headers:
        yield _emit(report, f"Заказ «{number}» в 1С не найден", on_log)
        yield _emit(report, "Импорт завершён", on_log)
        yield report
        return
    yield _emit(report, f"Шапок получено: {len(headers)}", on_log)
    if not by_number and len(headers) >= top:
        yield _emit(report, f"Внимание: достигнут лимит {top}, в 1С могут быть ещё заказы", on_log)
    if by_number:
        candidates = headers
        skipped = 0
    else:
        candidates = [doc for doc in headers if doc.state in CUSTOMER_STATES]
        skipped = len(headers) - len(candidates)
    report.other_status = skipped
    yield _emit(report, f"К импорту (статус «Отправка на склад»): {len(candidates)}", on_log)
    if skipped:
        yield _emit(report, f"Пропущено по статусу: {skipped}", on_log)
    if not allow_update and candidates:
        existing = _existing_customer_numbers([(doc.number or "").strip() for doc in candidates])
        before = len(candidates)
        candidates = _filter_new_headers(candidates, existing=existing, report=report)
        skipped_existing = before - len(candidates)
        if skipped_existing:
            yield _emit(report, f"Уже есть в WMS, пропущено: {skipped_existing}", on_log)
        yield _emit(report, f"К загрузке строк (только новые): {len(candidates)}", on_log)
    externals, aliases = _catalogs()
    total = len(candidates)
    for index, header in enumerate(candidates, start=1):
        label = header.number or header.ref_key or "?"
        yield _emit(report, f"Строки и справочники {label} — {index} из {total}…", on_log)
        try:
            doc = api.expand_customer(header)
        except Exception as exc:
            yield _emit(report, f"Ошибка загрузки {label}: {exc} — {index} из {total}", on_log)
            continue
        message = _sync_customer(
            doc,
            user,
            update=allow_update,
            externals=externals,
            aliases=aliases,
            report=report,
            any_status=by_number,
        )
        if message:
            yield _emit(report, f"{message} — {index} из {total}", on_log)
    yield _emit(report, "Импорт завершён", on_log)
    yield report


def delivery_period(date_from: date | None = None, date_to: date | None = None) -> tuple[date, date]:
    """Период поставки/отгрузки; по умолчанию — завтра."""
    tomorrow = date.today() + timedelta(days=1)
    start = date_from or date_to or tomorrow
    end = date_to or date_from or tomorrow
    if end < start:
        start, end = end, start
    return start, end


def _catalogs() -> tuple[dict[str, ExternalProduct], dict[str, object]]:
    externals = {item.code: item for item in ExternalProduct.objects.all()}
    aliases = {
        item.external_product.code: item.product
        for item in ProductAlias.objects.select_related("external_product", "product")
    }
    return externals, aliases


def _norm(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", value).strip()


def _match_seller(text: str) -> Seller | None:
    hay = _norm(text)
    if not hay:
        return None
    sellers = list(Seller.objects.all())
    for seller in sellers:
        name = _norm(seller.name)
        if len(name) >= 4 and name in hay:
            return seller
        if hay and name and (hay in name or name in hay):
            return seller
    for needle, key in SELLER_ALIASES:
        if needle not in hay:
            continue
        for seller in sellers:
            if key in _norm(seller.name):
                return seller
    return None


def _is_kg(unit: str) -> bool:
    value = (unit or "").strip().lower().replace(".", "")
    return value in {"кг", "kg", "кгг"}


def _line_dicts(
    doc: OrderDTO,
    *,
    externals: dict,
    aliases: dict,
    report: ImportReport,
    upsert_missing: bool,
) -> list[dict]:
    lines = []
    for item in doc.lines:
        name = item.product.name or item.content
        code = (item.product.code or "").strip()
        feedback = is_feedback_name(name)
        unit = (item.unit or "").strip() or "кг"
        if not _is_kg(unit) and not feedback:
            report.skipped_units.append({"order": doc.number, "name": name or "—", "unit": unit or "—"})
            continue
        qty = (item.qty or Decimal("0")).quantize(Decimal("0.001"))
        if qty <= 0:
            continue
        if not code:
            report.missing_codes.append({"order": doc.number, "code": "", "name": name or "нет кода"})
            continue
        card = externals.get(code)
        if card is None and (upsert_missing or feedback):
            card = upsert_external(code, name)
            externals[code] = card
        if card is None:
            report.missing_codes.append({"order": doc.number, "code": code, "name": name})
            continue
        ours = None if feedback else aliases.get(code)
        lines.append(
            {
                "product_id": ours.id if ours is not None else None,
                "external_product_id": card.id,
                "qty_kg": qty,
                "external_code": code,
                "external_name": name,
                "unit": "кг" if _is_kg(unit) else unit,
                "for_task": not feedback,
            }
        )
    return lines


def _purchase_row(order: PurchaseOrder, external: str, party: str, changes: dict | None = None) -> dict:
    return {
        "id": order.id,
        "number": order.number,
        "external": external,
        "party": party,
        "lines": order.lines.count(),
        "summary": revision_summary(changes or {}),
        "changes": changes or {},
    }


def _customer_row(order: Order, external: str, party: str, changes: dict | None = None) -> dict:
    return {
        "id": order.id,
        "number": order.number,
        "external": external,
        "party": party,
        "revision": order.revision,
        "lines": order.lines.count(),
        "summary": revision_summary(changes or {}),
        "changes": changes or {},
    }


@transaction.atomic
def _sync_purchase(
    doc: OrderDTO,
    user,
    *,
    update: bool,
    externals: dict,
    aliases: dict,
    report: ImportReport,
    any_status: bool = False,
) -> str:
    external = (doc.number or "").strip()
    if not external:
        return "Пропущен заказ без номера"
    status = PURCHASE_STATES.get(doc.state)
    if status is None:
        if not any_status:
            return f"Пропущен {external}: статус «{doc.state or '—'}»"
        status = "incoming"
    found = PurchaseOrder.objects.filter(external_number=external).select_related("supplier").first()
    if found is not None and not update:
        report.duplicates.append(external)
        return f"Пропущен {external}: уже есть в WMS"
    if not doc.party.name:
        report.missing_files.append(f"{external}: нет поставщика в 1С")
        return f"Пропущен {external}: нет поставщика в 1С"
    receipt = doc.receive_date or (doc.date.date() if doc.date else None)
    if receipt is None:
        report.missing_files.append(f"{external}: нет даты поступления")
        return f"Пропущен {external}: нет даты поступления"
    supplier = upsert_supplier(doc.party.name, doc.party.code)
    lines = _line_dicts(doc, externals=externals, aliases=aliases, report=report, upsert_missing=True)
    if not lines:
        report.empty.append(external)
        return f"Пропущен {external}: нет строк в кг"
    if found is None:
        order = PurchaseOrder.objects.create(
            external_number=external,
            supplier=supplier,
            doc_date=receipt,
            receipt_date=receipt,
            status=status,
            comment=doc.comment or "",
            created_by=user,
        )
        order.number = document_number("ЗП", order.id)
        order.save(update_fields=["number"])
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
        store_purchase_baseline(order, user)
        report.created.append(_purchase_row(order, external, supplier.name))
        return f"Создан заказ {order.number} ({external})"
    result = apply_purchase_import(
        found,
        user,
        supplier=supplier,
        doc_date=receipt,
        receipt_date=receipt,
        status=status,
        comment=doc.comment or "",
        lines=lines,
    )
    row = _purchase_row(found, external, supplier.name, result["changes"])
    kind = result["kind"]
    if kind == "updated":
        row["revision"] = result["revision"]
        report.updated.append(row)
        return f"Обновлён заказ {found.number} ({external})"
    if kind == "closed":
        report.closed.append(row)
        return f"Не обновлён {found.number} ({external}): закрыт или уже принят"
    if kind == "blocked":
        report.blocked.append(row)
        return f"Не обновлён {found.number} ({external}): есть блокировки"
    report.unchanged.append(row)
    return f"Без изменений {found.number} ({external})"


@transaction.atomic
def _sync_customer(
    doc: OrderDTO,
    user,
    *,
    update: bool,
    externals: dict,
    aliases: dict,
    report: ImportReport,
    any_status: bool = False,
) -> str:
    external = (doc.number or "").strip()
    if not external:
        return "Пропущен заказ без номера"
    if doc.state not in CUSTOMER_STATES and not any_status:
        return f"Пропущен {external}: статус «{doc.state or '—'}»"
    found = Order.objects.filter(external_number=external).select_related("consignee", "seller").first()
    if found is not None and not update:
        report.duplicates.append(external)
        return f"Пропущен {external}: уже есть в WMS"
    party_name = doc.consignee.name or doc.party.name
    party_code = doc.consignee.code or doc.party.code
    if not party_name:
        report.missing_files.append(f"{external}: нет грузополучателя в 1С")
        return f"Пропущен {external}: нет грузополучателя в 1С"
    ship_date = doc.ship_date or (doc.date.date() if doc.date else None)
    if ship_date is None:
        report.missing_files.append(f"{external}: нет даты отгрузки")
        return f"Пропущен {external}: нет даты отгрузки"
    seller = _match_seller(doc.organization.name)
    if seller is None:
        report.missing_files.append(f"{external}: нет продавца для «{doc.organization.name or '—'}»")
        return f"Пропущен {external}: нет продавца для «{doc.organization.name or '—'}»"
    address = (doc.delivery_address or "").strip()
    consignee = upsert_consignee(party_name, party_code, address=address)
    lines = _line_dicts(doc, externals=externals, aliases=aliases, report=report, upsert_missing=True)
    if not lines:
        report.empty.append(external)
        return f"Пропущен {external}: нет строк в кг"
    if found is not None:
        result = apply_import(
            found,
            user,
            consignee=consignee,
            seller=seller,
            ship_date=ship_date,
            terms=doc.comment or "",
            lines=lines,
        )
        row = _customer_row(found, external, consignee.name, result["changes"])
        kind = result["kind"]
        if kind == "updated":
            row["revision"] = result["revision"]
            report.updated.append(row)
            return f"Обновлён заказ {found.number} ({external})"
        if kind == "closed":
            report.closed.append(row)
            return f"Не обновлён {found.number} ({external}): заказ закрыт"
        if kind == "blocked":
            report.blocked.append(row)
            return f"Не обновлён {found.number} ({external}): есть блокировки"
        report.unchanged.append(row)
        return f"Без изменений {found.number} ({external})"
    order = create_order(
        consignee_id=consignee.id,
        seller_id=seller.id,
        ship_date=ship_date,
        shipping_terms=doc.comment or "",
        user=user,
        number=external,
    )
    for item in lines:
        OrderLine.objects.create(
            order=order,
            product_id=item["product_id"],
            external_product_id=item["external_product_id"],
            caliber_mode="any",
            qty_kg=item["qty_kg"],
            external_code=item["external_code"],
            external_name=item["external_name"],
            unit=item["unit"],
            for_task=item["for_task"],
        )
    store_baseline(order, user)
    report.created.append(_customer_row(order, external, consignee.name))
    return f"Создан заказ {order.number} ({external})"
