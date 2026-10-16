from pathlib import Path

from django.db import transaction

from apps.catalog.families import _base_name, critical_family, plain_title, with_pack
from apps.catalog.models import ExternalProduct, Product, ProductAlias, ProductGroup
from apps.catalog.parties import ensure_alias, upsert_external, warehouse_product
from apps.common.xlsx import read_rows


def stock_title(name: str) -> str:
    lowered = name.lower()
    if "морож" not in lowered:
        return critical_family(name)
    family = _base_name(name)
    if "морож" not in family.lower():
        family = f"{family} замороженная"
    return with_pack(name, plain_title(family))


def _catalog(source: Path | bytes) -> dict[str, dict[str, str]]:
    rows = read_rows(source, 1)
    header = rows.get(1) or {}
    columns = {name.strip(): column for column, name in header.items()}
    found = {}
    for number, cells in rows.items():
        if number == 1:
            continue
        code = (cells.get(columns.get("Код", ""), "") or "").strip()
        if not code:
            continue
        found[code] = {
            "name": (cells.get(columns.get("Наименование", ""), "") or "").strip(),
            "group": (cells.get(columns.get("В группе", ""), "") or "").strip(),
        }
    return found


def _used(source: Path | bytes) -> list[tuple[str, str]]:
    rows = []
    for cells in read_rows(source, 2).values():
        raw = (cells.get("A") or "").strip()
        code, _, name = raw.partition(",")
        code = code.strip()
        name = name.strip()
        if not code.startswith("НФ-") or not name:
            continue
        rows.append((code, name))
    return rows


def _group(name: str, groups: dict[str, ProductGroup]) -> ProductGroup | None:
    if not name:
        return None
    found = groups.get(name)
    if found is None:
        found = ProductGroup.objects.create(name=name)
        groups[name] = found
    return found


def _product(title: str, group: ProductGroup | None) -> Product:
    found = Product.objects.filter(name=title).first()
    if found is not None and group is not None and found.group_id not in (None, group.id):
        titled = f"{title} ({group.name})"[:120]
        found = Product.objects.filter(name=titled).first()
        title = titled
    if found is None:
        found = warehouse_product(title)
    if group is not None and found.group_id is None:
        found.group = group
        found.save(update_fields=["group"])
    return found


def _fill_known_groups(catalog: dict[str, dict[str, str]], used_codes: set[str], groups: dict[str, ProductGroup]) -> None:
    named: dict[int, set[str]] = {}
    aliases = ProductAlias.objects.filter(external_product__code__in=used_codes).select_related("external_product")
    for alias in aliases:
        group_name = (catalog.get(alias.external_product.code) or {}).get("group") or ""
        if group_name:
            named.setdefault(alias.product_id, set()).add(group_name)
    for product in Product.objects.filter(pk__in=named, group__isnull=True):
        names = named[product.id]
        if len(names) != 1:
            continue
        product.group = _group(next(iter(names)), groups)
        product.save(update_fields=["group"])


@transaction.atomic
def load_used_nomenclature(source: Path | bytes) -> dict[str, int]:
    catalog = _catalog(source)
    used = _used(source)
    groups = {item.name: item for item in ProductGroup.objects.all()}
    _fill_known_groups(catalog, {code for code, _name in used}, groups)
    created_external = 0
    created_product = 0
    created_alias = 0
    grouped = 0
    missing = 0
    products_before = set(Product.objects.values_list("id", flat=True))
    for code, name in used:
        card = catalog.get(code)
        if card is None:
            missing += 1
            continue
        existed = ExternalProduct.objects.filter(code=code).exists()
        external = upsert_external(code, card["name"] or name)
        if not existed:
            created_external += 1
        group = _group(card["group"], groups)
        if group is not None and external.group_id != group.id:
            external.group = group
            external.save(update_fields=["group"])
            grouped += 1
        if ProductAlias.objects.filter(external_product=external).exists():
            continue
        product = _product(stock_title(card["name"] or name), group)
        ensure_alias(external, product)
        created_alias += 1
        if product.id not in products_before:
            created_product += 1
            products_before.add(product.id)
    return {
        "used": len(used),
        "external": created_external,
        "products": created_product,
        "aliases": created_alias,
        "groups": grouped,
        "missing": missing,
    }
