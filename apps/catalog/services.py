from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.db.models import Case, F, IntegerField, Value, When

from apps.catalog.models import (
    Caliber,
    Client,
    Consignee,
    ConsigneeCategory,
    DeliveryZone,
    ExternalProduct,
    Grade,
    Packaging,
    Product,
    ProductAlias,
    ProductGroup,
    Seller,
    Supplier,
)
from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, text
from apps.common.kg import format_kg
from apps.printing.models import Printer
from apps.storage.models import Pallet

NAV = [
    ("Товары", [
        ("groups", "Группы"),
        ("products", "Номенклатура"),
        ("externals", "Номенклатура 1С"),
        ("aliases", "Соответствия"),
        ("calibers", "Калибры"),
        ("grades", "Сортность"),
        ("packagings", "Тара"),
    ]),
    ("Контрагенты", [
        ("suppliers", "Поставщики"),
        ("clients", "Контрагенты"),
        ("sellers", "Продавцы"),
        ("consignees", "Грузополучатели"),
        ("categories", "Категории"),
        ("consignee-grades", "Грузополучатель — сорт"),
    ]),
    ("Доставка", [
        ("zones", "Зоны доставки"),
        ("consignee-zones", "Грузополучатель — зона"),
    ]),
    ("Пользователи и права", [
        ("users", "Пользователи"),
        ("staff", "Сотрудники"),
        ("positions", "Должности"),
    ]),
    ("Печать", [
        ("printers", "Принтеры"),
    ]),
]
MODELS = {
    "groups": ProductGroup,
    "products": Product,
    "externals": ExternalProduct,
    "aliases": ProductAlias,
    "calibers": Caliber,
    "grades": Grade,
    "packagings": Packaging,
    "suppliers": Supplier,
    "clients": Client,
    "consignees": Consignee,
    "categories": ConsigneeCategory,
    "zones": DeliveryZone,
    "sellers": Seller,
    "printers": Printer,
}
TITLES = {key: label for _title, items in NAV for key, label in items}


def assign_consignee_grades(consignee, grade_ids: list[int]) -> str:
    wanted = list(dict.fromkeys(grade_ids))
    grades = list(Grade.objects.filter(pk__in=wanted).order_by("sort_order", "name"))
    if len(grades) != len(wanted):
        raise WarehouseError("Выберите сортность")
    consignee.allowed_grades.set(grades)
    if not grades:
        return "любой сорт"
    return ", ".join(item.name for item in grades)


def assign_consignee_zone(consignee, name: str) -> str:
    name = " ".join((name or "").split())
    if len(name) > 120:
        raise WarehouseError("Название зоны длиннее 120 символов")
    if not name:
        if consignee.zone_id:
            consignee.zone = None
            consignee.save(update_fields=["zone"])
        return ""
    zone = DeliveryZone.objects.filter(name__iexact=name).first()
    if zone is None:
        try:
            zone = DeliveryZone.objects.create(name=name)
        except IntegrityError:
            zone = DeliveryZone.objects.get(name=name)
    if consignee.zone_id != zone.id:
        consignee.zone = zone
        consignee.save(update_fields=["zone"])
    return zone.name


def ensure_category_grade(name: str) -> Grade:
    grade = Grade.objects.filter(name=name).first()
    if grade is not None:
        return grade
    last = Grade.objects.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0
    return Grade.objects.create(name=name, sort_order=last + 10)


def load_refs() -> dict:
    return {
        "products": list(Product.objects.order_by("name")),
        "calibers": list(Caliber.objects.order_by("sort_order", "name")),
        "grades": list(Grade.objects.order_by("sort_order", "name")),
        "packagings": list(Packaging.objects.order_by("name")),
        "suppliers": list(Supplier.objects.order_by("name")),
        "clients": list(Client.objects.order_by("name")),
        "consignees": list(Consignee.objects.select_related("category").order_by("name")),
        "categories": list(ConsigneeCategory.objects.order_by("sort_order", "name")),
        "sellers": list(Seller.objects.order_by("name")),
        "open_pallets": list(Pallet.objects.filter(kind="inbound", status="open").order_by("-id")),
    }


def _alias_rows():
    headers = ["Код 1С", "Номенклатура 1С", "Код", "Номенклатура"]
    aliases = {
        item.external_product_id: item
        for item in ProductAlias.objects.select_related("product", "external_product")
    }
    rows = []
    for external in ExternalProduct.objects.order_by("code"):
        alias = aliases.get(external.id)
        if alias is not None:
            cells = [external.code, external.name, alias.product.code, alias.product.name]
            href = f"/masters/aliases/{alias.id}"
            row_id = alias.id
        else:
            cells = [external.code, external.name, "", ""]
            href = f"/masters/aliases/new?external_product_id={external.id}"
            row_id = external.id
        rows.append({"id": row_id, "cells": cells, "href": href})
    return headers, rows


def master_rows(kind: str):
    model = MODELS[kind]
    if kind == "products":
        items = model.objects.select_related("group").order_by("code")
    elif kind == "externals":
        items = model.objects.select_related("group").order_by("code")
    elif kind == "aliases":
        return _alias_rows()
    elif kind == "consignees":
        items = (
            model.objects.select_related("zone", "category")
            .annotate(
                delivery_rank=Case(
                    When(delivery_order=0, then=Value(1_000_000)),
                    default="delivery_order",
                    output_field=IntegerField(),
                )
            )
            .order_by(F("zone__name").asc(nulls_last=True), "delivery_rank", "name")
        )
    elif kind == "categories":
        items = model.objects.select_related("grade").order_by("sort_order", "name")
    elif kind in ("groups", "zones"):
        items = model.objects.order_by("name")
    else:
        items = model.objects.order_by("id")
    rows = []
    for item in items:
        if kind == "products":
            cells = [item.code, item.barcode or "", item.group.name if item.group_id else "", item.name]
        elif kind == "externals":
            cells = [item.code, item.group.name if item.group_id else "", item.name]
        elif kind in ("calibers", "grades"):
            cells = [item.name, str(item.sort_order)]
        elif kind == "clients":
            cells = [item.code or "", item.name]
        elif kind == "suppliers":
            cells = [item.code or "", item.name]
        elif kind == "consignees":
            terms = (item.shipping_terms or "").splitlines()[0] if item.shipping_terms else ""
            cells = [
                item.code or "",
                item.name,
                item.category.name if item.category_id else "",
                item.address,
                item.zone.name if item.zone_id else "",
                str(item.delivery_order) if item.delivery_order else "",
                terms,
            ]
        elif kind == "categories":
            cells = [item.name, str(item.sort_order), item.grade.name]
        elif kind == "sellers":
            cells = [item.code, item.name]
        elif kind == "packagings":
            cells = [item.name, format_kg(item.tare_kg)]
        elif kind == "printers":
            cells = [
                item.name,
                "TSC" if item.printer_type == "tsc" else "Сетевой",
                item.host or item.queue_name,
                "да" if item.is_default else "",
            ]
        else:
            cells = [item.name]
        rows.append({"id": item.id, "cells": cells})
    headers = {
        "products": ["Код", "ШК", "Группа", "Название"],
        "externals": ["Код", "Группа", "Название"],
        "aliases": ["Код 1С", "Номенклатура 1С", "Код", "Номенклатура"],
        "calibers": ["Калибр", "Порядок"],
        "grades": ["Сортность", "Порядок"],
        "sellers": ["Код", "Название"],
        "packagings": ["Название", "Вес тары, кг"],
        "clients": ["Код", "Контрагент"],
        "suppliers": ["Код", "Поставщик"],
        "consignees": ["Код", "Грузополучатель", "Категория", "Адрес", "Зона доставки", "Очередность", "Условия отгрузки"],
        "categories": ["Категория", "Порядок", "Сортность"],
        "zones": ["Зона доставки"],
        "printers": ["Имя", "Тип", "Адрес", "По умолчанию"],
    }.get(kind, ["Название"])
    return headers, rows


def apply_master(kind: str, item, form) -> None:
    if kind == "printers":
        from apps.printing.services import apply_printer

        apply_printer(item, form)
        return
    if kind == "aliases":
        external = ExternalProduct.objects.filter(pk=optional_int(form, "external_product_id") or 0).first()
        product = Product.objects.filter(pk=optional_int(form, "product_id") or 0).first()
        if external is None or product is None:
            raise WarehouseError("Выберите номенклатуру 1С и номенклатуру склада")
        taken = ProductAlias.objects.filter(external_product=external).exclude(pk=item.pk or 0).first()
        if taken is not None:
            raise WarehouseError("Соответствие для этого кода уже есть")
        item.external_product = external
        item.product = product
        item.save()
        return
    name = text(form, "name")
    if not name:
        raise WarehouseError("Укажите название")
    item.name = name
    if kind in ("calibers", "grades", "categories"):
        item.sort_order = optional_int(form, "sort_order") or 0
    if kind == "categories":
        taken = ConsigneeCategory.objects.filter(name=item.name).exclude(pk=item.pk or 0).exists()
        if taken:
            raise WarehouseError("Такая категория уже есть")
        item.grade = ensure_category_grade(item.name)
    if kind == "products":
        item.code = text(form, "code")
        item.barcode = text(form, "barcode")
        if not item.code:
            raise WarehouseError("Укажите код")
        if not item.barcode:
            raise WarehouseError("Укажите штрихкод")
        if Product.objects.filter(code=item.code).exclude(pk=item.pk or 0).exists():
            raise WarehouseError("Такой код уже есть")
        if Product.objects.filter(barcode=item.barcode).exclude(pk=item.pk or 0).exists():
            raise WarehouseError("Такой штрихкод уже есть")
        group_id = optional_int(form, "group_id")
        if group_id is not None and not ProductGroup.objects.filter(pk=group_id).exists():
            raise WarehouseError("Выберите группу")
        item.group_id = group_id
    if kind == "externals":
        item.code = text(form, "code")
        if not item.code:
            raise WarehouseError("Укажите код")
        if ExternalProduct.objects.filter(code=item.code).exclude(pk=item.pk or 0).exists():
            raise WarehouseError("Такой код уже есть")
        group_id = optional_int(form, "group_id")
        if group_id is not None and not ProductGroup.objects.filter(pk=group_id).exists():
            raise WarehouseError("Выберите группу")
        item.group_id = group_id
    if kind == "clients":
        item.code = text(form, "code") or None
        if item.code and Client.objects.filter(code=item.code).exclude(pk=item.pk or 0).exists():
            raise WarehouseError("Такой код уже есть")
    if kind == "suppliers":
        item.code = text(form, "code") or None
        if item.code and Supplier.objects.filter(code=item.code).exclude(pk=item.pk or 0).exists():
            raise WarehouseError("Такой код уже есть")
    if kind == "consignees":
        item.code = text(form, "code") or None
        if item.code and Consignee.objects.filter(code=item.code).exclude(pk=item.pk or 0).exists():
            raise WarehouseError("Такой код уже есть")
        item.address = text(form, "address")
        item.shipping_terms = text(form, "shipping_terms")
        zone_id = optional_int(form, "zone_id")
        if zone_id and not DeliveryZone.objects.filter(pk=zone_id).exists():
            raise WarehouseError("Выберите зону доставки")
        item.zone_id = zone_id
        delivery_order = optional_int(form, "delivery_order")
        if delivery_order is None:
            item.delivery_order = 0
        elif delivery_order < 0:
            raise WarehouseError("Очередность не может быть отрицательной")
        else:
            item.delivery_order = delivery_order
        category_id = optional_int(form, "category_id")
        if category_id and not ConsigneeCategory.objects.filter(pk=category_id).exists():
            raise WarehouseError("Выберите категорию")
        item.category_id = category_id
    if kind == "sellers":
        item.code = text(form, "code")
        if not item.code:
            raise WarehouseError("Укажите код продавца")
        item.requisites = text(form, "requisites")
    if kind == "packagings":
        raw = text(form, "tare_kg").replace(",", ".")
        if not raw:
            item.tare_kg = Decimal("0.000")
        else:
            try:
                tare = Decimal(raw).quantize(Decimal("0.001"))
            except InvalidOperation as exc:
                raise WarehouseError("Некорректный вес тары") from exc
            if tare < 0:
                raise WarehouseError("Вес тары не может быть отрицательным")
            item.tare_kg = tare
    item.save()
    if kind == "categories":
        from apps.catalog.grade_policy import fill_consignee_orders

        for consignee in Consignee.objects.filter(category=item).prefetch_related("allowed_grades"):
            fill_consignee_orders(consignee)
    if kind == "consignees":
        raw_ids = []
        for raw in form.getlist("allowed_grade"):
            try:
                raw_ids.append(int(raw))
            except (TypeError, ValueError) as exc:
                raise WarehouseError("Выберите сортность") from exc
        grades = list(Grade.objects.filter(pk__in=raw_ids).order_by("sort_order", "name"))
        if len(grades) != len(set(raw_ids)):
            raise WarehouseError("Выберите сортность")
        item.allowed_grades.set(grades)
        from apps.catalog.grade_policy import fill_consignee_orders

        fill_consignee_orders(item)
