from pathlib import Path

from apps.catalog.families import critical_family
from apps.catalog.parties import ensure_alias, upsert_consignee, upsert_external, warehouse_product
from apps.common.xlsx import read_rows
from apps.planning.import_customer import _cell, _columns, _detail_files, _detail_rows, _order_key, is_feedback_name


def sync_customer_catalog(source: Path | bytes) -> None:
    rows = read_rows(source)
    header = rows.get(1) or {}
    columns = _columns(header)
    for number in sorted(row for row in rows if row != 1):
        cells = rows[number]
        buyer = _cell(cells, columns, "Покупатель")
        buyer_code = _cell(cells, columns, "КодПокупатель")
        consignee_name = _cell(cells, columns, "Грузополучатель") or buyer
        consignee_code = _cell(cells, columns, "Код") or buyer_code
        if not consignee_name:
            continue
        upsert_consignee(consignee_name, consignee_code)
        key = _order_key(_cell(cells, columns, "Номер"))
        files = _detail_files(key) if key is not None else []
        if len(files) != 1:
            continue
        for item in _detail_rows(files[0]):
            if not item["code"]:
                continue
            feedback = is_feedback_name(item["name"])
            if item["unit"] != "кг" and not feedback:
                continue
            external = upsert_external(item["code"], item["name"])
            if feedback:
                continue
            product = warehouse_product(critical_family(item["name"] or item["code"]))
            ensure_alias(external, product)
