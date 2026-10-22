from django.shortcuts import get_object_or_404, render

from apps.catalog.services import load_refs
from apps.common.exceptions import WarehouseError
from apps.common.forms import require_int, text
from apps.common.http import done, guarded, roles_required
from apps.common.present import group_units
from apps.storage.models import HandlingUnit, Pallet
from apps.storage.services import (
    add_to_inbound,
    add_to_outbound,
    change_grade,
    close_pallet,
    create_inbound_pallet,
    detach_unit,
    find_by_barcode,
    reject_unit,
)


@roles_required("receiver", "warehouse")
def pallet_list(request):
    built = request.GET.get("run") == "1"
    pallets = Pallet.objects.exclude(kind="loose").select_related("order", "reserved_client").order_by("-id") if built else []
    return render(request, "pallets/list.html", {"pallets": pallets, "built": built})


@roles_required("receiver", "warehouse")
def pallet_create(request):
    def action():
        return create_inbound_pallet(user=request.user, location=text(request.POST, "location"))

    result, response = guarded(request, "/pallets", action)
    if response:
        return response
    request.session["current_pallet_id"] = result.id
    return done(request, f"/pallets/{result.id}", "Входная паллета открыта")


@roles_required("receiver", "warehouse")
def pallet_detail(request, pallet_id):
    pallet = get_object_or_404(Pallet.objects.select_related("order__client", "reserved_client"), pk=pallet_id)
    units = list(pallet.units.select_related("lot__product", "lot__caliber", "lot__supplier", "grade", "packaging").order_by("id"))
    spent = (
        pallet.movements.filter(kind__in=("transform_out", "waste"))
        .select_related("transform_document")
        .order_by("id")
        .first()
    )
    return render(
        request,
        "pallets/detail.html",
        {"pallet": pallet, "units": units, "groups": group_units(units), "spent": spent},
    )


@roles_required("receiver", "warehouse")
def pallet_location(request, pallet_id):
    def action():
        pallet = Pallet.objects.filter(pk=pallet_id).first()
        if pallet is None:
            raise WarehouseError("Паллета не найдена")
        pallet.location = text(request.POST, "location")
        pallet.save(update_fields=["location"])

    result, response = guarded(request, f"/pallets/{pallet_id}", action)
    if response:
        return response
    return done(request, f"/pallets/{pallet_id}", "Место хранения сохранено")


@roles_required("receiver", "warehouse")
def pallet_close(request, pallet_id):
    def action():
        pallet = Pallet.objects.filter(pk=pallet_id).first()
        if pallet is None:
            raise WarehouseError("Паллета не найдена")
        close_pallet(pallet, request.user)

    result, response = guarded(request, f"/pallets/{pallet_id}", action)
    if response:
        return response
    return done(request, f"/pallets/{pallet_id}", "Паллета закрыта")


@roles_required("receiver", "warehouse")
def pallet_add(request, pallet_id):
    def action():
        pallet = Pallet.objects.filter(pk=pallet_id).first()
        if pallet is None:
            raise WarehouseError("Паллета не найдена")
        kind, obj = find_by_barcode(text(request.POST, "barcode"))
        if kind != "unit":
            raise WarehouseError("Сканируйте штрихкод грузоместа")
        if pallet.kind == "inbound":
            add_to_inbound(pallet, obj)
        else:
            add_to_outbound(pallet, obj)
        return obj

    result, response = guarded(request, f"/pallets/{pallet_id}", action)
    if response:
        return response
    return done(request, f"/pallets/{pallet_id}", f"Место {result.barcode} добавлено")


@roles_required("receiver", "warehouse")
def unit_detail(request, unit_id):
    unit = get_object_or_404(
        HandlingUnit.objects.select_related(
            "lot__product", "lot__caliber", "lot__supplier", "grade", "packaging", "pallet", "receipt"
        ),
        pk=unit_id,
    )
    return render(request, "units/detail.html", {"unit": unit, "grades": load_refs()["grades"]})


@roles_required("receiver", "warehouse")
def unit_detach(request, unit_id):
    def action():
        unit = HandlingUnit.objects.filter(pk=unit_id).first()
        if unit is None:
            raise WarehouseError("Место не найдено")
        detach_unit(unit)

    result, response = guarded(request, f"/units/{unit_id}", action)
    if response:
        return response
    return done(request, f"/units/{unit_id}", "Место снято с паллеты")


@roles_required("receiver", "warehouse")
def unit_quality(request, unit_id):
    def action():
        unit = HandlingUnit.objects.filter(pk=unit_id).select_related("pallet").first()
        if unit is None:
            raise WarehouseError("Место не найдено")
        change_grade(unit, require_int(request.POST, "grade_id", "Сортность"))

    result, response = guarded(request, f"/units/{unit_id}", action)
    if response:
        return response
    return done(request, f"/units/{unit_id}", "Сортность обновлена")


@roles_required("receiver", "warehouse")
def unit_reject(request, unit_id):
    def action():
        unit = HandlingUnit.objects.filter(pk=unit_id).select_related("pallet").first()
        if unit is None:
            raise WarehouseError("Место не найдено")
        reject_unit(unit, text(request.POST, "reason"))

    result, response = guarded(request, f"/units/{unit_id}", action)
    if response:
        return response
    return done(request, f"/units/{unit_id}", "Место отмечено как брак")
