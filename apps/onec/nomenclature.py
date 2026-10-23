"""Номенклатура 1С: отчёт и группы наших товаров."""

from __future__ import annotations

from collections import defaultdict

from django.db import transaction

from apps.catalog.models import ExternalProduct, Product, ProductAlias, ProductGroup
from apps.onec.client import EMPTY_GUID, ODataClient
from apps.onec.exceptions import OnecRequestError

GROUP_NAMES = {
    "борщевой набор": "БОРЩ",
    "очищенные овощи": "Очищенка",
    "цитрусы и тропические": "ФРОВ",
}

PAGE = 1000
NOMENCLATURE_SELECT = (
    "Ref_Key,Code,Description,Артикул,IsFolder,Parent_Key,DeletionMark,ЕдиницаИзмерения_Key"
)
UNIT_SELECT = "Ref_Key,Description"


def nomenclature_rows(client: ODataClient | None = None) -> list[dict]:
    client = client or ODataClient.from_settings()
    rows = _pages(client, "Catalog_Номенклатура", NOMENCLATURE_SELECT)
    units = {
        str(row.get("Ref_Key") or ""): str(row.get("Description") or "")
        for row in _pages(client, "Catalog_КлассификаторЕдиницИзмерения", UNIT_SELECT)
    }
    by_key = {str(row.get("Ref_Key") or ""): row for row in rows if row.get("Ref_Key")}
    report = []
    for row in rows:
        path = _group_path(row, by_key)
        unit_key = str(row.get("ЕдиницаИзмерения_Key") or "")
        report.append(
            {
                "code": str(row.get("Code") or ""),
                "name": str(row.get("Description") or ""),
                "article": str(row.get("Артикул") or ""),
                "group": path[-1] if path else "",
                "path": " / ".join(path),
                "unit": units.get(unit_key, ""),
                "folder": bool(row.get("IsFolder")),
                "deleted": bool(row.get("DeletionMark")),
            }
        )
    report.sort(key=lambda item: (item["path"], item["folder"] is False, item["code"], item["name"]))
    return report


def _pages(client: ODataClient, entity: str, select: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    skip = 0
    while skip < 200_000:
        try:
            page = client.get(entity, select=select, orderby="Code", top=PAGE, skip=skip)
        except OnecRequestError:
            if skip:
                break
            page = client.get(entity, select=select, top=PAGE, skip=skip)
        if not page:
            break
        first = str(page[0].get("Ref_Key") or "")
        if first and first in seen:
            break
        found.extend(page)
        for row in page:
            key = str(row.get("Ref_Key") or "")
            if key:
                seen.add(key)
        if len(page) < PAGE:
            break
        skip += PAGE
    return found


@transaction.atomic
def assign_product_groups(client: ODataClient | None = None) -> dict:
    """Ставит нашим товарам группу из папки номенклатуры 1С. Расхождение групп не затирает."""
    by_code = {
        row["code"]: _warehouse_group(row["group"])
        for row in nomenclature_rows(client)
        if row["code"] and row["group"] and not row["folder"] and not row["deleted"]
    }
    groups = {item.name: item for item in ProductGroup.objects.all()}

    def group_for(name: str) -> ProductGroup:
        found = groups.get(name)
        if found is None:
            found = ProductGroup.objects.create(name=name)
            groups[name] = found
        return found

    named: dict[int, set[str]] = defaultdict(set)
    aliases = ProductAlias.objects.select_related("external_product")
    for alias in aliases:
        name = by_code.get(alias.external_product.code)
        if name:
            named[alias.product_id].add(name)
    updated = 0
    skipped = 0
    for product in Product.objects.filter(pk__in=named):
        names = named[product.id]
        if len(names) != 1:
            skipped += 1
            continue
        group = group_for(next(iter(names)))
        if product.group_id == group.id:
            continue
        product.group = group
        product.save(update_fields=["group"])
        updated += 1

    external_updated = 0
    for external in ExternalProduct.objects.filter(code__in=by_code):
        group = group_for(by_code[external.code])
        if external.group_id == group.id:
            continue
        external.group = group
        external.save(update_fields=["group"])
        external_updated += 1
    for old_name in GROUP_NAMES:
        leftover = ProductGroup.objects.filter(name__iexact=old_name).first()
        if leftover is None:
            continue
        if leftover.products.exists() or leftover.external_products.exists():
            continue
        leftover.delete()
    return {
        "products": updated,
        "skipped": skipped,
        "external": external_updated,
        "groups": ProductGroup.objects.count(),
    }


def _warehouse_group(name: str) -> str:
    return GROUP_NAMES.get(name.casefold(), name)[:120]


def _group_path(row: dict, by_key: dict[str, dict]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    parent = str(row.get("Parent_Key") or "")
    while parent and parent != EMPTY_GUID and parent not in seen:
        seen.add(parent)
        folder = by_key.get(parent)
        if folder is None:
            break
        title = str(folder.get("Description") or "").strip()
        if title:
            names.append(title)
        parent = str(folder.get("Parent_Key") or "")
    names.reverse()
    return names
