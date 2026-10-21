from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.onec import catalogs
from apps.onec.client import ODataClient
from apps.onec.dto import OrderDTO, OrderLineDTO, PartyRef
from apps.onec.exceptions import OnecNotFound

ENTITY_PURCHASE = "Document_ЗаказПоставщику"
ENTITY_PURCHASE_LINES = "Document_ЗаказПоставщику_Запасы"
ENTITY_CUSTOMER = "Document_ЗаказПокупателя"
ENTITY_CUSTOMER_LINES = "Document_ЗаказПокупателя_Запасы"

PURCHASE_SELECT = (
    "Ref_Key,Number,Date,Posted,DeletionMark,Комментарий,Контрагент_Key,"
    "Организация_Key,СостояниеЗаказа_Key,ДатаПоступления,СуммаДокумента"
)
CUSTOMER_SELECT = (
    "Ref_Key,Number,Date,Posted,DeletionMark,Комментарий,Контрагент_Key,"
    "Организация_Key,Грузополучатель_Key,СостояниеЗаказа,СостояниеЗаказа_Type,"
    "ДатаОтгрузки,СуммаДокумента,АдресДоставки,СпособДоставки"
)


class OnecOrders:
    """Прослойка: заказы поставщикам и покупателям из УНФ через OData."""

    def __init__(self, client: ODataClient | None = None):
        self.client = client or ODataClient.from_settings()

    def list_purchase_orders(
        self,
        *,
        top: int = 50,
        skip: int = 0,
        posted: bool | None = True,
        number: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        with_lines: bool = True,
        resolve: bool = True,
    ) -> list[OrderDTO]:
        rows = self.client.get(
            ENTITY_PURCHASE,
            filter=_filter(
                posted=posted,
                number=number,
                date_from=date_from,
                date_to=date_to,
                date_field="ДатаПоступления",
            ),
            select=PURCHASE_SELECT,
            orderby="ДатаПоступления desc",
            top=top,
            skip=skip,
        )
        states = None if resolve else catalogs.states_map(self.client, kind="purchase")
        return [
            self._map_purchase(row, with_lines=with_lines, resolve=resolve, states=states)
            for row in rows
        ]

    def get_purchase_order(self, ref_key: str, *, with_lines: bool = True) -> OrderDTO:
        try:
            row = self.client.get_one(ENTITY_PURCHASE, ref_key, select=PURCHASE_SELECT)
        except OnecNotFound as exc:
            raise OnecNotFound(f"Заказ поставщику {ref_key} не найден") from exc
        return self._map_purchase(row, with_lines=with_lines, resolve=True)

    def expand_purchase(self, stub: OrderDTO) -> OrderDTO:
        """Строки и справочники по уже загруженной шапке (без повторного GET документа)."""
        if not stub.raw:
            return self.get_purchase_order(stub.ref_key, with_lines=True)
        return self._map_purchase(stub.raw, with_lines=True, resolve=True)

    def find_purchase_by_number(self, number: str, *, with_lines: bool = True, resolve: bool = True) -> OrderDTO | None:
        rows = self.list_purchase_orders(
            top=1, posted=None, number=number, with_lines=with_lines, resolve=resolve
        )
        return rows[0] if rows else None

    def list_customer_orders(
        self,
        *,
        top: int = 50,
        skip: int = 0,
        posted: bool | None = True,
        number: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        with_lines: bool = True,
        resolve: bool = True,
    ) -> list[OrderDTO]:
        rows = self.client.get(
            ENTITY_CUSTOMER,
            filter=_filter(
                posted=posted,
                number=number,
                date_from=date_from,
                date_to=date_to,
                date_field="ДатаОтгрузки",
            ),
            select=CUSTOMER_SELECT,
            orderby="ДатаОтгрузки desc",
            top=top,
            skip=skip,
        )
        states = None if resolve else catalogs.states_map(self.client, kind="customer")
        return [
            self._map_customer(row, with_lines=with_lines, resolve=resolve, states=states)
            for row in rows
        ]

    def get_customer_order(self, ref_key: str, *, with_lines: bool = True) -> OrderDTO:
        try:
            row = self.client.get_one(ENTITY_CUSTOMER, ref_key, select=CUSTOMER_SELECT)
        except OnecNotFound as exc:
            raise OnecNotFound(f"Заказ покупателя {ref_key} не найден") from exc
        return self._map_customer(row, with_lines=with_lines, resolve=True)

    def expand_customer(self, stub: OrderDTO) -> OrderDTO:
        """Строки и справочники по уже загруженной шапке (без повторного GET документа)."""
        if not stub.raw:
            return self.get_customer_order(stub.ref_key, with_lines=True)
        return self._map_customer(stub.raw, with_lines=True, resolve=True)

    def find_customer_by_number(self, number: str, *, with_lines: bool = True, resolve: bool = True) -> OrderDTO | None:
        rows = self.list_customer_orders(
            top=1, posted=None, number=number, with_lines=with_lines, resolve=resolve
        )
        return rows[0] if rows else None

    def _map_purchase(
        self,
        row: dict[str, Any],
        *,
        with_lines: bool,
        resolve: bool = True,
        states: dict[str, str] | None = None,
    ) -> OrderDTO:
        party_key = catalogs.ref_guid(row, "Контрагент_Key", "Контрагент")
        org_key = catalogs.ref_guid(row, "Организация_Key", "Организация")
        state = catalogs.order_state_label(row, kind="purchase", client=self.client, states=states)
        if resolve:
            party = catalogs.party(self.client, party_key)
            organization = catalogs.organization(self.client, org_key)
        else:
            party = PartyRef(ref_key=party_key)
            organization = PartyRef(ref_key=org_key)
        dto = OrderDTO(
            kind="purchase",
            ref_key=str(row.get("Ref_Key") or ""),
            number=str(row.get("Number") or ""),
            date=_parse_datetime(row.get("Date")),
            posted=bool(row.get("Posted")),
            party=party,
            organization=organization,
            state=state,
            comment=str(row.get("Комментарий") or ""),
            receive_date=_parse_date(row.get("ДатаПоступления")),
            amount=_decimal(row.get("СуммаДокумента")),
            raw=row,
        )
        if with_lines and dto.ref_key:
            dto.lines = self._purchase_lines(dto.ref_key)
        return dto

    def _map_customer(
        self,
        row: dict[str, Any],
        *,
        with_lines: bool,
        resolve: bool = True,
        states: dict[str, str] | None = None,
    ) -> OrderDTO:
        party_key = catalogs.ref_guid(row, "Контрагент_Key", "Контрагент")
        org_key = catalogs.ref_guid(row, "Организация_Key", "Организация")
        consignee_key = catalogs.ref_guid(row, "Грузополучатель_Key", "Грузополучатель")
        state = catalogs.order_state_label(row, kind="customer", client=self.client, states=states)
        if resolve:
            party = catalogs.party(self.client, party_key)
            organization = catalogs.organization(self.client, org_key)
            consignee = catalogs.party(self.client, consignee_key) if consignee_key else PartyRef()
        else:
            party = PartyRef(ref_key=party_key)
            organization = PartyRef(ref_key=org_key)
            consignee = PartyRef(ref_key=consignee_key) if consignee_key else PartyRef()
        dto = OrderDTO(
            kind="customer",
            ref_key=str(row.get("Ref_Key") or ""),
            number=str(row.get("Number") or ""),
            date=_parse_datetime(row.get("Date")),
            posted=bool(row.get("Posted")),
            party=party,
            organization=organization,
            consignee=consignee,
            delivery_address=" ".join(str(row.get("АдресДоставки") or "").split()),
            state=state,
            comment=str(row.get("Комментарий") or ""),
            ship_date=_parse_date(row.get("ДатаОтгрузки")),
            amount=_decimal(row.get("СуммаДокумента")),
            raw=row,
        )
        if with_lines and dto.ref_key:
            dto.lines = self._customer_lines(dto.ref_key)
        return dto

    def _purchase_lines(self, ref_key: str) -> list[OrderLineDTO]:
        rows = self.client.get(
            ENTITY_PURCHASE_LINES,
            filter=f"Ref_Key eq guid'{ref_key}'",
            orderby="LineNumber",
        )
        return [self._map_line(row, product_fields=("Номенклатура_Key", "Номенклатура")) for row in rows]

    def _customer_lines(self, ref_key: str) -> list[OrderLineDTO]:
        rows = self.client.get(
            ENTITY_CUSTOMER_LINES,
            filter=f"Ref_Key eq guid'{ref_key}'",
            orderby="LineNumber",
        )
        return [self._map_line(row, product_fields=("Номенклатура", "Номенклатура_Key")) for row in rows]

    def _map_line(self, row: dict[str, Any], *, product_fields: tuple[str, ...]) -> OrderLineDTO:
        product_key = catalogs.ref_guid(row, *product_fields)
        unit_key = catalogs.ref_guid(row, "ЕдиницаИзмерения", "ЕдиницаИзмерения_Key")
        return OrderLineDTO(
            line_number=int(row.get("LineNumber") or 0),
            product=catalogs.product(self.client, product_key),
            qty=_decimal(row.get("Количество")) or Decimal("0"),
            unit=catalogs.unit_name(self.client, unit_key),
            content=str(row.get("Содержание") or ""),
            price=_decimal(row.get("Цена")),
            amount=_decimal(row.get("Сумма") or row.get("Всего")),
            weight=_decimal(row.get("Вес")),
            raw=row,
        )


def _filter(
    *,
    posted: bool | None,
    number: str | None,
    date_from: date | None,
    date_to: date | None,
    date_field: str = "Date",
) -> str | None:
    parts: list[str] = ["DeletionMark eq false"]
    if posted is True:
        parts.append("Posted eq true")
    elif posted is False:
        parts.append("Posted eq false")
    if number:
        safe = number.replace("'", "''")
        parts.append(f"Number eq '{safe}'")
    if date_from:
        parts.append(f"{date_field} ge datetime'{date_from.isoformat()}T00:00:00'")
    if date_to:
        nxt = date_to + timedelta(days=1)
        parts.append(f"{date_field} lt datetime'{nxt.isoformat()}T00:00:00'")
    return " and ".join(parts) if parts else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    dt = _parse_datetime(value)
    return dt.date() if dt else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
