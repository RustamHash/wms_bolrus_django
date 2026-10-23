from apps.catalog.families import CRITICAL_PRODUCTS, FAMILY_CODE, critical_family
from apps.catalog.models import Consignee, ExternalProduct, Product, ProductAlias, Supplier
from apps.common.exceptions import WarehouseError


def _clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def upsert_supplier(name: str, code: str = "") -> Supplier:
    name = _clean(name, 200)
    code = _clean(code, 32)
    if not name:
        raise WarehouseError("Укажите поставщика")
    found = Supplier.objects.filter(code=code).first() if code else None
    if found is None:
        found = Supplier.objects.filter(name=name).first()
    if found is None:
        return Supplier.objects.create(name=name, code=code or None)
    changed = []
    if found.name != name:
        found.name = name
        changed.append("name")
    if code and found.code != code:
        found.code = code
        changed.append("code")
    if changed:
        found.save(update_fields=changed)
    return found


def upsert_consignee(name: str, code: str = "", address: str = "") -> Consignee:
    name = _clean(name, 200)
    code = _clean(code, 32)
    address = _clean(address, 1000)
    if not name:
        raise WarehouseError("Укажите грузополучателя")
    found = Consignee.objects.filter(code=code).first() if code else None
    if found is None:
        named = Consignee.objects.filter(name=name).first()
        if named is not None and (not code or not named.code or named.code == code):
            found = named
    if found is None:
        return Consignee.objects.create(name=name, code=code or None, address=address)
    changed = []
    if found.name != name:
        found.name = name
        changed.append("name")
    if code and found.code != code:
        found.code = code
        changed.append("code")
    if address and found.address != address:
        found.address = address
        changed.append("address")
    if changed:
        found.save(update_fields=changed)
    return found


def load_consignee_addresses(source) -> dict:
    from apps.common.xlsx import read_rows

    rows = read_rows(source)
    header = rows.get(1) or {}
    columns = {value.strip(): key for key, value in header.items()}

    def cell(row: dict, title: str) -> str:
        key = columns.get(title)
        if not key:
            return ""
        return (row.get(key) or "").strip()

    def moment(row: dict) -> float:
        raw = cell(row, "Дата").replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            return 0.0

    parties = {}
    buyers = {}
    for number, row in rows.items():
        if number == 1:
            continue
        buyer_name = cell(row, "Покупатель / Поставщик")
        buyer_code = cell(row, "Код Покупателя")
        if buyer_name or buyer_code:
            buyers[buyer_code or buyer_name] = (buyer_name, buyer_code)
        address = _clean(cell(row, "Адрес доставки"), 1000)
        if not address:
            continue
        code = cell(row, "Код Грузополучателя")
        name = cell(row, "Грузополучатель")
        if not code and not name:
            code = buyer_code
            name = buyer_name
        if not code and not name:
            continue
        record = (name, code, address, moment(row), number)
        key = code or name
        previous = parties.get(key)
        if previous is None or (record[3], record[4]) >= (previous[3], previous[4]):
            parties[key] = record

    created = 0
    updated = 0
    unchanged = 0
    for name, code, address, _moment, _number in parties.values():
        existed = Consignee.objects.filter(code=code).first() if code else None
        if existed is None:
            existed = Consignee.objects.filter(name=_clean(name, 200)).first()
        is_new = existed is None
        consignee = upsert_consignee(name, code)
        if is_new:
            created += 1
        if consignee.address == address:
            unchanged += 1
            continue
        consignee.address = address
        consignee.save(update_fields=["address"])
        updated += 1
    return {
        "consignees": len(parties),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
    }


def _next_product_code() -> str:
    numbers = []
    for code, _name in CRITICAL_PRODUCTS:
        if code.startswith("Т-") and code[2:].isdigit():
            numbers.append(int(code[2:]))
    for code in Product.objects.values_list("code", flat=True):
        if code.startswith("Т-") and code[2:].isdigit():
            numbers.append(int(code[2:]))
    return f"Т-{max(numbers, default=0) + 1:03d}"


def warehouse_product(family: str) -> Product:
    family = _clean(family, 120)
    found = Product.objects.filter(name=family).first()
    if found is not None:
        return found
    code = FAMILY_CODE.get(family) or _next_product_code()
    taken = Product.objects.filter(code=code).first()
    if taken is not None:
        return taken
    return Product.objects.create(name=family, code=code)


def upsert_external(code: str, name: str) -> ExternalProduct:
    code = _clean(code, 32)
    name = _clean(name, 120) or code
    found = ExternalProduct.objects.filter(code=code).first()
    if found is None:
        return ExternalProduct.objects.create(code=code, name=name)
    if name and found.name != name:
        found.name = name
        found.save(update_fields=["name"])
    return found


def ensure_alias(external: ExternalProduct, product: Product) -> None:
    alias = ProductAlias.objects.filter(external_product=external).first()
    if alias is None:
        ProductAlias.objects.create(external_product=external, product=product)
        return
    if alias.product_id != product.id:
        alias.product = product
        alias.save(update_fields=["product"])
