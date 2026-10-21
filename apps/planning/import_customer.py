import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from apps.catalog.models import Client, ExternalProduct, ProductAlias, Seller
from apps.catalog.parties import consignee_for_buyer, upsert_client, upsert_consignee, upsert_external
from apps.common.exceptions import WarehouseError
from apps.common.xlsx import read_rows
from apps.planning.models import Order, OrderLine
from apps.planning.revisions import apply_import, revision_summary, store_baseline
from apps.planning.services import create_order

ORDERS_DIR = Path(r"C:\Users\Server\Documents\orders")
INCOMING = "Отправка на склад"
CODE = re.compile(r"^НФ-\d+$")
HEADER = ("Дата", "Номер", "Состояние", "Покупатель", "Дата отгрузки")
SELLER_ALIASES = (
    ("большая русь", "болрус"),
    ("болрус", "болрус"),
    ("байчора", "байчора"),
)


@dataclass
class ImportReport:
    created: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    unchanged: list[dict] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    missing_codes: list[dict] = field(default_factory=list)
    skipped_units: list[dict] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    other_status: int = 0
    log: list[str] = field(default_factory=list)


def _parse_date(raw: str) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return date(1899, 12, 30) + timedelta(days=int(float(value)))
    return None


def _columns(header: dict[str, str]) -> dict[str, str]:
    return {value.strip(): key for key, value in header.items()}


def _cell(row: dict[str, str], columns: dict[str, str], title: str) -> str:
    key = columns.get(title)
    if not key:
        return ""
    return (row.get(key) or "").strip()


def _order_key(number: str) -> int | None:
    match = re.search(r"(\d+)$", number.replace(" ", ""))
    if match is None:
        return None
    return int(match.group(1))


def _file_date(path: Path) -> date | None:
    match = re.search(r"от\s+(\d{2}\.\d{2}\.\d{4})", path.name)
    if match is None:
        return None
    return _parse_date(match.group(1))


def _detail_files(key: int) -> list[Path]:
    token = f"№ НФ-{key} от"
    if not ORDERS_DIR.is_dir():
        return []
    return sorted(path for path in ORDERS_DIR.glob("Заказ покупателя*.xlsx") if token in path.name)


def is_feedback_name(name: str) -> bool:
    value = " ".join((name or "").lower().replace("ё", "е").split())
    return value == "транспортные услуги" or value.startswith("транспортные услуги ")


def _detail_rows(path: Path) -> list[dict]:
    rows = []
    for cells in read_rows(path).values():
        code = (cells.get("R") or "").strip()
        if not CODE.match(code):
            continue
        name = " ".join((cells.get("G") or "").split())
        unit = (cells.get("X") or "").strip()
        qty = (cells.get("U") or "").strip().replace(",", ".")
        rows.append({"code": code, "name": name, "unit": unit, "qty": qty})
    return rows


def _client(name: str, code: str = "") -> Client:
    return upsert_client(name, code)


def _norm(value: str) -> str:
    value = value.lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", value).strip()


def _executor(path: Path) -> str:
    for cells in read_rows(path).values():
        label = (cells.get("B") or "").strip()
        if label.startswith("Исполнитель"):
            return " ".join((cells.get("F") or "").split())
    return ""


def _seller(text: str) -> Seller | None:
    hay = _norm(text)
    sellers = list(Seller.objects.all())
    for seller in sellers:
        name = _norm(seller.name)
        if len(name) >= 4 and name in hay:
            return seller
        for token in re.findall(r"\d{10,12}", seller.requisites or ""):
            if token in text:
                return seller
    for needle, key in SELLER_ALIASES:
        if needle not in hay:
            continue
        for seller in sellers:
            if key in _norm(seller.name):
                return seller
    return None


def _order_row(order: Order, external: str, party: str, changes: dict | None = None) -> dict:
    return {
        "id": order.id,
        "number": order.number,
        "external": external,
        "party": party,
        "revision": order.revision,
        "summary": revision_summary(changes or {}),
        "changes": changes or {},
    }


def import_customer_orders(source: Path | bytes, user, update: bool = False) -> ImportReport:
    rows = read_rows(source)
    header = rows.get(1) or {}
    columns = _columns(header)
    missing = [name for name in HEADER if name not in columns]
    if missing:
        raise WarehouseError("В шапке нет колонок: " + ", ".join(missing))
    from apps.catalog.from_orders import sync_customer_catalog

    sync_customer_catalog(source)
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
        if _cell(cells, columns, "Состояние") != INCOMING:
            report.other_status += 1
            continue
        found = Order.objects.filter(external_number=external).select_related("client", "consignee", "seller").first()
        if found is not None and not update:
            report.duplicates.append(external)
            continue
        key = _order_key(external)
        files = _detail_files(key) if key is not None else []
        if len(files) != 1:
            reason = "несколько файлов" if len(files) > 1 else "нет файла"
            report.missing_files.append(f"{external}: {reason}")
            continue
        path = files[0]
        ship_date = _parse_date(_cell(cells, columns, "Дата отгрузки")) or _file_date(path) or _parse_date(_cell(cells, columns, "Дата"))
        if ship_date is None:
            report.missing_files.append(f"{external}: нет даты отгрузки")
            continue
        client_name = _cell(cells, columns, "Покупатель")
        client_code = _cell(cells, columns, "КодПокупатель")
        if not client_name:
            report.missing_files.append(f"{external}: нет покупателя")
            continue
        if len(client_name) > 200:
            report.missing_files.append(f"{external}: слишком длинное имя покупателя")
            continue
        client = _client(client_name, client_code)
        consignee_name = _cell(cells, columns, "Грузополучатель")
        consignee_code = _cell(cells, columns, "Код")
        if not consignee_name and not consignee_code:
            consignee = consignee_for_buyer(client)
        else:
            consignee = upsert_consignee(consignee_name or client.name, consignee_code or client.code or "")
        seller = _seller(_executor(path))
        if seller is None:
            report.missing_files.append(f"{external}: нет продавца")
            continue
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
            if card is None and feedback:
                card = upsert_external(item["code"], item["name"])
                externals[item["code"]] = card
            if card is None:
                report.missing_codes.append({"order": external, "code": item["code"], "name": item["name"]})
                continue
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
        if not lines:
            report.empty.append(external)
            continue
        if found is not None:
            result = apply_import(
                found,
                user,
                client=client,
                consignee=consignee,
                seller=seller,
                ship_date=ship_date,
                terms=_cell(cells, columns, "Комментарий"),
                lines=lines,
            )
            row = _order_row(found, external, client_name, result["changes"])
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
            continue
        order = create_order(
            client_id=client.id,
            seller_id=seller.id,
            ship_date=ship_date,
            shipping_terms=_cell(cells, columns, "Комментарий"),
            user=user,
            consignee_id=consignee.id,
        )
        order.external_number = external
        order.save(update_fields=["external_number"])
        for item in lines:
            OrderLine.objects.create(
                order=order,
                product_id=item["product_id"],
                external_product_id=item["external_product_id"],
                caliber_mode="any",
                qty_kg=item["qty_kg"],
                external_code=item["external_code"],
                external_name=item["external_name"],
                unit=item.get("unit") or "кг",
                for_task=item.get("for_task", True),
            )
        from apps.catalog.grade_policy import stamp_order_grades

        stamp_order_grades(order)
        store_baseline(order, user)
        report.created.append(
            {
                "id": order.id,
                "number": order.number,
                "external": external,
                "party": client_name,
                "lines": len(lines),
            }
        )
    return report
