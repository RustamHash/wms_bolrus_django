from __future__ import annotations

import re
from typing import Any

from apps.onec.client import EMPTY_GUID, ODataClient
from apps.onec.dto import PartyRef, ProductRef

PARTY_SELECT = "Ref_Key,Code,Description,НаименованиеПолное"
PRODUCT_SELECT = "Ref_Key,Code,Description,Артикул"
UNIT_SELECT = "Ref_Key,Code,Description"
STATE_PURCHASE = "Catalog_СостоянияЗаказовПоставщикам"
STATE_CUSTOMER = "Catalog_СостоянияЗаказовПокупателей"
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_guid(value: str) -> bool:
    return bool(value) and value != EMPTY_GUID and bool(_GUID_RE.match(value))


def ref_guid(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and value and value != EMPTY_GUID:
            return value
        if isinstance(value, dict):
            nested = value.get("Ref_Key") or value.get("Key")
            if isinstance(nested, str) and nested and nested != EMPTY_GUID:
                return nested
    return ""


def party(client: ODataClient, guid: str) -> PartyRef:
    row = client.get_cached("Catalog_Контрагенты", guid, select=PARTY_SELECT)
    if not row:
        return PartyRef(ref_key=guid)
    return PartyRef(
        ref_key=str(row.get("Ref_Key") or guid),
        code=str(row.get("Code") or ""),
        name=str(row.get("Description") or row.get("НаименованиеПолное") or ""),
    )


def organization(client: ODataClient, guid: str) -> PartyRef:
    row = client.get_cached("Catalog_Организации", guid, select="Ref_Key,Code,Description")
    if not row:
        return PartyRef(ref_key=guid)
    return PartyRef(
        ref_key=str(row.get("Ref_Key") or guid),
        code=str(row.get("Code") or ""),
        name=str(row.get("Description") or ""),
    )


def product(client: ODataClient, guid: str) -> ProductRef:
    row = client.get_cached("Catalog_Номенклатура", guid, select=PRODUCT_SELECT)
    if not row:
        return ProductRef(ref_key=guid)
    return ProductRef(
        ref_key=str(row.get("Ref_Key") or guid),
        code=str(row.get("Code") or ""),
        name=str(row.get("Description") or ""),
        article=str(row.get("Артикул") or ""),
    )


def unit_name(client: ODataClient, guid: str) -> str:
    if not guid or guid == EMPTY_GUID:
        return ""
    row = client.get_cached("Catalog_КлассификаторЕдиницИзмерения", guid, select=UNIT_SELECT)
    if row:
        return str(row.get("Description") or row.get("Code") or "")
    # иногда единица хранится как Catalog_ЕдиницыИзмерения
    row = client.get_cached("Catalog_ЕдиницыИзмерения", guid, select=UNIT_SELECT)
    if row:
        return str(row.get("Description") or row.get("Code") or "")
    return ""


def states_map(client: ODataClient, *, kind: str) -> dict[str, str]:
    """Все состояния заказов одним запросом: Ref_Key → наименование."""
    entity = STATE_PURCHASE if kind == "purchase" else STATE_CUSTOMER
    cache_key = f"states_map:{entity}"
    cached = client._cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("__map__"):
        return cached["__map__"]
    rows = client.get(entity, select="Ref_Key,Description")
    mapping = {
        str(row.get("Ref_Key") or ""): str(row.get("Description") or "")
        for row in rows
        if row.get("Ref_Key")
    }
    client._cache[cache_key] = {"__map__": mapping}
    return mapping


def state_name(
    client: ODataClient,
    guid: str,
    *,
    kind: str,
    states: dict[str, str] | None = None,
) -> str:
    if not guid or guid == EMPTY_GUID:
        return ""
    if states is not None:
        return states.get(guid, "")
    entity = STATE_PURCHASE if kind == "purchase" else STATE_CUSTOMER
    row = client.get_cached(entity, guid, select="Ref_Key,Description")
    if not row:
        return ""
    return str(row.get("Description") or "")


def order_state_label(
    row: dict[str, Any],
    *,
    kind: str,
    client: ODataClient | None = None,
    states: dict[str, str] | None = None,
) -> str:
    """Имя состояния из строки документа: строка, GUID или справочник."""
    if kind == "purchase":
        key = ref_guid(row, "СостояниеЗаказа_Key", "СостояниеЗаказа")
        if states is not None:
            return states.get(key, "")
        if client is not None and key:
            return state_name(client, key, kind=kind)
        return ""
    value = row.get("СостояниеЗаказа")
    key = ref_guid(row, "СостояниеЗаказа", "СостояниеЗаказа_Key")
    if isinstance(value, str) and value and not is_guid(value):
        return value
    lookup = key or (value if isinstance(value, str) else "")
    if states is not None:
        return states.get(lookup, "")
    if client is not None and lookup:
        return state_name(client, lookup, kind=kind)
    return str(value or "")
