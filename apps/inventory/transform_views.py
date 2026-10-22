from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.catalog.models import Caliber, Grade, Product
from apps.common.exceptions import WarehouseError
from apps.common.forms import parse_date, parse_kg, require_int, text
from apps.common.http import done, guarded, roles_required
from apps.inventory.models import TransformDocument
from apps.inventory.transform import (
    add_input,
    add_output,
    add_waste,
    cancel_document,
    create_document,
    delete_input,
    delete_output,
    delete_waste,
    mass_state,
    parse_tolerance,
    post_document,
    save_header,
    stock_choices,
)


def _documents():
    return TransformDocument.objects.annotate(
        input_count=Count("inputs"),
        output_count=Count("outputs"),
    ).order_by("-doc_date", "-id")


def _optional_date(raw: str):
    if not raw:
        return None
    return parse_date(raw, "Срок годности")


@roles_required("office")
def transform_list(request):
    return render(
        request,
        "transform/list.html",
        {"title": "Переработка", "documents": _documents()},
    )


@roles_required("office")
def transform_new(request):
    kind = text(request.GET, "kind") or TransformDocument.Kind.REWORK
    if kind not in TransformDocument.Kind.values:
        kind = TransformDocument.Kind.REWORK
    return render(
        request,
        "transform/new.html",
        {"title": "Новая переработка", "today": timezone.localdate().isoformat(), "kind": kind},
    )


@roles_required("office")
def transform_create(request):
    def action():
        return create_document(
            kind=text(request.POST, "kind"),
            doc_date=parse_date(text(request.POST, "doc_date"), "Дата"),
            tolerance_kg=parse_tolerance(text(request.POST, "tolerance_kg")),
            notes=text(request.POST, "notes"),
            user=request.user,
        )

    result, response = guarded(request, "/transform/new", action)
    if response:
        return response
    return redirect(f"/transform/{result.id}")


def _detail_context(document: TransformDocument) -> dict:
    inputs = list(
        document.inputs.select_related("pallet", "lot__product", "lot__caliber", "lot__grade", "lot__supplier").order_by(
            "id"
        )
    )
    outputs = list(
        document.outputs.select_related("product", "caliber", "grade", "pallet", "lot").order_by("id")
    )
    wastes = list(document.wastes.order_by("id"))
    state = mass_state(document)
    open_doc = document.status == TransformDocument.Status.DRAFT
    return {
        "title": document.number,
        "document": document,
        "inputs": inputs,
        "outputs": outputs,
        "wastes": wastes,
        "open": open_doc,
        "choices": stock_choices(document) if open_doc else [],
        "products": Product.objects.order_by("name") if open_doc else [],
        "calibers": Caliber.objects.order_by("sort_order", "name") if open_doc else [],
        "grades": Grade.objects.order_by("sort_order", "name") if open_doc else [],
        **state,
    }


@roles_required("office")
def transform_detail(request, doc_id):
    document = get_object_or_404(TransformDocument, pk=doc_id)
    return render(request, "transform/detail.html", _detail_context(document))


@roles_required("office")
def transform_action(request, doc_id):
    document = get_object_or_404(TransformDocument, pk=doc_id)
    back = f"/transform/{document.id}"

    def action():
        kind = text(request.POST, "action")
        if kind == "save":
            save_header(
                document,
                doc_date=parse_date(text(request.POST, "doc_date"), "Дата"),
                tolerance_kg=parse_tolerance(text(request.POST, "tolerance_kg")),
                notes=text(request.POST, "notes"),
            )
            return "Документ сохранён"
        if kind == "post":
            return post_document(document, request.user)
        if kind == "cancel":
            cancel_document(document)
            return "Документ отменён"
        raise WarehouseError("Некорректное действие")

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, result)


@roles_required("office")
def transform_add_input(request, doc_id):
    document = get_object_or_404(TransformDocument, pk=doc_id)
    back = f"/transform/{document.id}"

    def action():
        add_input(document, require_int(request.POST, "pallet_id", "Паллета"))
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Паллета добавлена")


@roles_required("office")
def transform_add_output(request, doc_id):
    document = get_object_or_404(TransformDocument, pk=doc_id)
    back = f"/transform/{document.id}"

    def action():
        add_output(
            document,
            product_id=require_int(request.POST, "product_id", "Товар"),
            caliber_id=require_int(request.POST, "caliber_id", "Калибр"),
            grade_id=require_int(request.POST, "grade_id", "Сорт"),
            qty_kg=parse_kg(text(request.POST, "qty_kg"), "Вес"),
            expires_on=_optional_date(text(request.POST, "expires_on")),
        )
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Выход добавлен")


@roles_required("office")
def transform_add_waste(request, doc_id):
    document = get_object_or_404(TransformDocument, pk=doc_id)
    back = f"/transform/{document.id}"

    def action():
        add_waste(document, qty_kg=parse_kg(text(request.POST, "qty_kg"), "Вес"), reason=text(request.POST, "reason"))
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Отход добавлен")


def _delete(request, doc_id, line_id, fn, message):
    document = get_object_or_404(TransformDocument, pk=doc_id)
    back = f"/transform/{document.id}"

    def action():
        fn(document, line_id)
        return None

    _, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, message)


@roles_required("office")
def transform_delete_input(request, doc_id, line_id):
    return _delete(request, doc_id, line_id, delete_input, "Паллета убрана")


@roles_required("office")
def transform_delete_output(request, doc_id, line_id):
    return _delete(request, doc_id, line_id, delete_output, "Выход убран")


@roles_required("office")
def transform_delete_waste(request, doc_id, line_id):
    return _delete(request, doc_id, line_id, delete_waste, "Отход убран")
