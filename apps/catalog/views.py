from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.catalog.models import Consignee, ConsigneeCategory, DeliveryZone, ExternalProduct, Grade, Product, ProductGroup
from apps.catalog.services import MODELS, NAV, TITLES, apply_master, assign_consignee_grades, assign_consignee_zone, master_rows
from apps.common.exceptions import WarehouseError
from apps.common.http import done, guarded, roles_required


@roles_required("office")
def office_home(request):
    return redirect("/masters")


@roles_required("office")
def masters_home(request):
    return redirect("/masters/products")


@roles_required("office")
def consignee_zones(request):
    consignees = Consignee.objects.select_related("zone").order_by("name")
    zones = list(DeliveryZone.objects.order_by("name").values_list("name", flat=True))
    return render(
        request,
        "masters/consignee_zones.html",
        {
            "kind": "consignee-zones",
            "title": "Грузополучатель — зона доставки",
            "nav": NAV,
            "consignees": consignees,
            "zones": zones,
        },
    )


@roles_required("office")
def consignee_zone_save(request, consignee_id):
    consignee = Consignee.objects.filter(pk=consignee_id).first()
    if consignee is None:
        return JsonResponse({"ok": False, "error": "Грузополучатель не найден"}, status=404)
    try:
        zone = assign_consignee_zone(consignee, request.POST.get("zone", ""))
    except WarehouseError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "zone": zone})


@roles_required("office")
def consignee_grades(request):
    consignees = Consignee.objects.prefetch_related("allowed_grades").order_by("name")
    grades = list(Grade.objects.order_by("sort_order", "name"))
    return render(
        request,
        "masters/consignee_grades.html",
        {
            "kind": "consignee-grades",
            "title": "Грузополучатель — сортность",
            "nav": NAV,
            "consignees": consignees,
            "grades": grades,
        },
    )


@roles_required("office")
def consignee_grades_save(request, consignee_id):
    consignee = Consignee.objects.filter(pk=consignee_id).first()
    if consignee is None:
        return JsonResponse({"ok": False, "error": "Грузополучатель не найден"}, status=404)
    raw_ids = []
    for raw in request.POST.getlist("grade"):
        try:
            raw_ids.append(int(raw))
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Выберите сортность"}, status=400)
    try:
        names = assign_consignee_grades(consignee, raw_ids)
    except WarehouseError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "grades": names})


@roles_required("office")
def master_list(request, kind):
    if kind == "users":
        return redirect("/users")
    if kind not in MODELS:
        raise Http404
    headers, rows = master_rows(kind)
    return render(
        request,
        "masters/list.html",
        {
            "kind": kind,
            "title": TITLES[kind],
            "headers": headers,
            "rows": rows,
            "nav": NAV,
            "can_change": kind != "categories" or request.user.role == "admin",
        },
    )


def _form_context(kind, item, *, external_product_id=None):
    data = {
        "kind": kind,
        "title": TITLES[kind],
        "item": item,
        "nav": NAV,
        "products": list(Product.objects.order_by("name")),
        "externals": list(ExternalProduct.objects.order_by("code")),
        "groups": list(ProductGroup.objects.order_by("name")),
        "zones": list(DeliveryZone.objects.order_by("name")),
        "grades": list(Grade.objects.order_by("sort_order", "name")),
        "categories": list(ConsigneeCategory.objects.order_by("sort_order", "name")),
        "external_product_id": external_product_id,
        "installed_printers": [],
        "installed_error": "",
    }
    if kind == "printers":
        from apps.printing.installed import list_installed

        try:
            data["installed_printers"] = list_installed()
        except Exception as exc:
            data["installed_error"] = str(exc)
    return data


def _category_locked(request, kind):
    if kind != "categories" or request.user.role == "admin":
        return None
    messages.error(request, "Категории меняет только администратор")
    return redirect("/masters/categories")


@roles_required("office")
def master_new(request, kind):
    if kind not in MODELS:
        raise Http404
    locked = _category_locked(request, kind)
    if locked:
        return locked
    external_product_id = None
    if kind == "aliases":
        try:
            external_product_id = int(request.GET.get("external_product_id") or 0) or None
        except ValueError:
            external_product_id = None
    return render(request, "masters/form.html", _form_context(kind, None, external_product_id=external_product_id))


@roles_required("office")
def master_create(request, kind):
    if kind not in MODELS:
        raise Http404
    locked = _category_locked(request, kind)
    if locked:
        return locked

    def action():
        item = MODELS[kind]()
        apply_master(kind, item, request.POST)
        return item

    result, response = guarded(request, f"/masters/{kind}/new", action)
    if response:
        return response
    return done(request, f"/masters/{kind}", "Сохранено")


@roles_required("office")
def master_edit(request, kind, item_id):
    if kind not in MODELS:
        raise Http404
    locked = _category_locked(request, kind)
    if locked:
        return locked
    item = get_object_or_404(MODELS[kind], pk=item_id)
    return render(request, "masters/form.html", _form_context(kind, item))


@roles_required("office")
def master_update(request, kind, item_id):
    if kind not in MODELS:
        raise Http404
    locked = _category_locked(request, kind)
    if locked:
        return locked

    def action():
        item = MODELS[kind].objects.filter(pk=item_id).first()
        if item is None:
            raise WarehouseError("Запись не найдена")
        apply_master(kind, item, request.POST)
        return item

    result, response = guarded(request, f"/masters/{kind}/{item_id}", action)
    if response:
        return response
    return done(request, f"/masters/{kind}", "Сохранено")


@roles_required("office")
def master_delete(request, kind, item_id):
    if kind not in MODELS:
        raise Http404
    locked = _category_locked(request, kind)
    if locked:
        return locked

    def action():
        item = MODELS[kind].objects.filter(pk=item_id).first()
        if item is None:
            raise WarehouseError("Запись не найдена")
        item.delete()

    result, response = guarded(request, f"/masters/{kind}", action)
    if response:
        return response
    return done(request, f"/masters/{kind}", "Удалено")
