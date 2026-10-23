from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.catalog.models import Caliber, Grade, Product, Supplier
from apps.common.exceptions import WarehouseError
from apps.common.forms import parse_date, text
from apps.common.http import done, guarded, roles_required
from apps.inventory.count import (
    add_found,
    cancel_document,
    create_document,
    delete_found,
    parse_fact_kg,
    post_document,
    save_count,
    start_document,
)
from apps.inventory.models import InventoryDocument, InventoryLine
from apps.storage.places import place_form_context, resolve_place


def _documents():
    return InventoryDocument.objects.annotate(
        line_count=Count("lines"),
        counted_count=Count("lines", filter=Q(lines__fact_kg__isnull=False)),
    ).order_by("-doc_date", "-id")


def _lines(document: InventoryDocument):
    return document.lines.select_related(
        "pallet",
        "lot__product",
        "lot__caliber",
        "lot__grade",
        "lot__supplier",
        "supplier",
        "product",
        "caliber",
        "grade",
        "counted_by",
        "cell__zone",
    ).order_by("id")


@roles_required("office")
def inventory_list(request):
    return render(
        request,
        "inventory/list.html",
        {"title": "Инвентаризация", "documents": _documents()},
    )


@roles_required("office")
def inventory_new(request):
    return render(
        request,
        "inventory/new.html",
        {
            "title": "Новая инвентаризация",
            "products": Product.objects.order_by("name"),
            "today": timezone.localdate().isoformat(),
        },
    )


@roles_required("office")
def inventory_create(request):
    def action():
        scope = text(request.POST, "scope") or InventoryDocument.Scope.ALL
        raw_ids = request.POST.getlist("product_id")
        product_ids = []
        for raw in raw_ids:
            if not raw:
                continue
            try:
                product_ids.append(int(raw))
            except ValueError as exc:
                raise WarehouseError("Некорректная номенклатура") from exc
        if scope == InventoryDocument.Scope.PRODUCTS:
            known = set(Product.objects.filter(pk__in=product_ids).values_list("pk", flat=True))
            if len(known) != len(set(product_ids)):
                raise WarehouseError("Номенклатура не найдена")
        else:
            product_ids = []
        return create_document(
            doc_date=parse_date(text(request.POST, "doc_date"), "Дата"),
            scope=scope,
            product_ids=product_ids,
            notes=text(request.POST, "notes"),
            user=request.user,
        )

    result, response = guarded(request, "/inventory/new", action)
    if response:
        return response
    return redirect(f"/inventory/{result.id}")


def _detail_context(document: InventoryDocument) -> dict:
    lines = list(_lines(document))
    return {
        "title": document.number,
        "document": document,
        "lines": lines,
        "suppliers": Supplier.objects.order_by("name"),
        "products": document.products.order_by("name"),
        "all_products": Product.objects.order_by("name"),
        "calibers": Caliber.objects.order_by("sort_order", "name"),
        "grades": Grade.objects.order_by("sort_order", "name"),
        "counted": sum(1 for line in lines if line.fact_kg is not None),
        "open": document.status == InventoryDocument.Status.COUNTING,
        **place_form_context(),
    }


@roles_required("office")
def inventory_detail(request, doc_id):
    document = get_object_or_404(InventoryDocument, pk=doc_id)
    return render(request, "inventory/detail.html", _detail_context(document))


@roles_required("office")
def inventory_action(request, doc_id):
    document = get_object_or_404(InventoryDocument, pk=doc_id)
    back = f"/inventory/{document.id}"

    def action():
        kind = text(request.POST, "action")
        if kind == "start":
            count = start_document(document)
            return f"Подсчёт начат. Паллет в снимке: {count}"
        if kind == "post":
            post_document(document, request.user)
            return "Инвентаризация проведена"
        if kind == "cancel":
            cancel_document(document)
            return "Инвентаризация отменена"
        raise WarehouseError("Некорректное действие")

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, result)


@roles_required("office")
def inventory_save_line(request, doc_id):
    document = get_object_or_404(InventoryDocument, pk=doc_id)
    back = f"/inventory/{document.id}"

    def action():
        line = InventoryLine.objects.filter(pk=request.POST.get("line_id"), document=document).first()
        if line is None:
            raise WarehouseError("Строка не найдена")
        raw = text(request.POST, "fact_kg")
        fact = parse_fact_kg(raw) if raw else None
        supplier_raw = text(request.POST, "supplier_id")
        try:
            supplier_id = int(supplier_raw)
        except ValueError as exc:
            raise WarehouseError("Выберите поставщика") from exc
        save_count(line, fact_kg=fact, supplier_id=supplier_id, user=request.user, fact_given=bool(raw))
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Строка сохранена")


def _choice_id(form, name: str, label: str) -> int:
    raw = text(form, name)
    try:
        return int(raw)
    except ValueError as exc:
        raise WarehouseError(f"Выберите {label}") from exc


@roles_required("office")
def inventory_add_found(request, doc_id):
    document = get_object_or_404(InventoryDocument, pk=doc_id)
    back = f"/inventory/{document.id}"

    def action():
        add_found(
            document,
            product_id=_choice_id(request.POST, "product_id", "товар"),
            caliber_id=_choice_id(request.POST, "caliber_id", "калибр"),
            grade_id=_choice_id(request.POST, "grade_id", "сорт"),
            supplier_id=_choice_id(request.POST, "supplier_id", "поставщика"),
            cell=resolve_place(request.POST, required=True),
            qty_kg=parse_fact_kg(text(request.POST, "fact_kg")),
            user=request.user,
        )
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Паллета добавлена. На остаток встанет после проводки")


@roles_required("office")
def inventory_delete_found(request, doc_id, line_id):
    document = get_object_or_404(InventoryDocument, pk=doc_id)
    back = f"/inventory/{document.id}"

    def action():
        line = InventoryLine.objects.filter(pk=line_id, document=document).first()
        if line is None:
            raise WarehouseError("Строка не найдена")
        delete_found(line)
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Строка удалена")
