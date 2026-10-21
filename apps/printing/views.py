from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect

from apps.common.exceptions import WarehouseError
from apps.common.forms import text
from apps.common.http import back_url, roles_required
from apps.planning.models import Task
from apps.printing import labels, pdfs
from apps.printing.models import Printer
from apps.printing.services import print_pallet, print_shipment, print_unit, send_pdf, send_tspl
from apps.shipping.models import Shipment
from apps.storage.models import HandlingUnit, Pallet


def _pdf(content: bytes, filename: str) -> HttpResponse:
    return HttpResponse(content, content_type="application/pdf", headers={"Content-Disposition": f"inline; filename={filename}"})


def load_print_tasks(ids: list[int]):
    rows = Task.objects.filter(pk__in=ids).select_related(
        "order__client",
        "order__consignee",
        "order__seller",
        "purchase_order__supplier",
        "supplier",
    ).prefetch_related(
        "lines__product__group",
        "lines__caliber",
        "lines__grade",
        "lines__order_line__caliber_from",
        "lines__order_line__caliber_to",
    )
    found = {item.id: item for item in rows}
    return [found[item] for item in ids if item in found]


def task_sheet_response(tasks, *, by_groups: bool = False) -> HttpResponse:
    filename = "tasks.pdf" if len(tasks) > 1 else f"task-{tasks[0].id}.pdf"
    return _pdf(pdfs.tasks_pdf(tasks, by_groups=by_groups), filename)


@roles_required("receiver", "warehouse")
def task_sheet(request, task_id):
    tasks = load_print_tasks([task_id])
    if not tasks:
        return HttpResponse("Не найдено", status=404)
    return task_sheet_response(tasks, by_groups=text(request.GET, "print_groups") == "1")


def _fail(request, target, message):
    messages.error(request, message)
    return redirect(target)


def _ok(request, target, message):
    messages.success(request, message)
    return redirect(target)


@roles_required("receiver", "warehouse")
def unit_pdf(request, unit_id):
    unit = HandlingUnit.objects.filter(pk=unit_id).select_related("lot__product", "lot__caliber", "lot__supplier", "grade", "packaging").first()
    if unit is None:
        return HttpResponse("Не найдено", status=404)
    return _pdf(pdfs.unit_label_pdf(unit), f"unit-{unit.id}.pdf")


@roles_required("receiver", "warehouse")
def unit_print(request, unit_id):
    unit = HandlingUnit.objects.filter(pk=unit_id).select_related("lot__product", "lot__caliber", "lot__supplier", "grade", "packaging").first()
    target = back_url(request, f"/units/{unit_id}")
    if unit is None:
        return _fail(request, target, "Место не найдено")
    try:
        message = print_unit(unit, text(request.POST, "target"))
    except WarehouseError as exc:
        return _fail(request, target, str(exc))
    return _ok(request, target, message)


@roles_required("receiver", "warehouse")
def pallet_pdf(request, pallet_id):
    pallet = Pallet.objects.filter(pk=pallet_id).select_related("order__client", "reserved_client", "closed_by", "created_by").prefetch_related(
        "units__lot__product", "units__lot__caliber", "units__lot__supplier", "units__grade", "units__packaging"
    ).first()
    if pallet is None:
        return HttpResponse("Не найдено", status=404)
    try:
        content = pdfs.pallet_passport_pdf(pallet)
    except WarehouseError as exc:
        return HttpResponse(str(exc), status=400)
    return _pdf(content, f"pallet-{pallet.id}.pdf")


@roles_required("receiver", "warehouse")
def pallet_print(request, pallet_id):
    pallet = Pallet.objects.filter(pk=pallet_id).select_related("order__client", "reserved_client").prefetch_related("units__lot__product", "units__lot__caliber", "units__grade").first()
    target = back_url(request, f"/pallets/{pallet_id}")
    if pallet is None:
        return _fail(request, target, "Паллета не найдена")
    try:
        message = print_pallet(pallet, text(request.POST, "target"))
    except WarehouseError as exc:
        return _fail(request, target, str(exc))
    return _ok(request, target, message)


@roles_required("warehouse")
def sticker_pdf(request, shipment_id):
    shipment = Shipment.objects.filter(pk=shipment_id).prefetch_related("units__lot__product", "units__lot__caliber", "units__grade").first()
    if shipment is None:
        return HttpResponse("Не найдено", status=404)
    return _pdf(pdfs.sticker_pdf(shipment, list(shipment.units.all())), f"sticker-{shipment.id}.pdf")


@roles_required("warehouse")
def waybill_pdf(request, shipment_id):
    shipment = Shipment.objects.filter(pk=shipment_id).select_related("order").prefetch_related(
        "units__lot__product", "units__lot__caliber", "units__grade"
    ).first()
    if shipment is None:
        return HttpResponse("Не найдено", status=404)
    return _pdf(pdfs.waybill_pdf(shipment, list(shipment.units.all())), f"waybill-{shipment.id}.pdf")


@roles_required("warehouse")
def shipment_print(request, shipment_id):
    shipment = Shipment.objects.filter(pk=shipment_id).prefetch_related("units__lot__product", "units__lot__caliber", "units__grade").first()
    target = back_url(request, f"/shipments/{shipment_id}")
    if shipment is None:
        return _fail(request, target, "Накладная не найдена")
    try:
        message = print_shipment(shipment, text(request.POST, "target"), text(request.POST, "document"))
    except WarehouseError as exc:
        return _fail(request, target, str(exc))
    return _ok(request, target, message)


def load_print_routes(ids: list[int]):
    from apps.logistics.models import Route
    from apps.logistics.queries import order_weights

    routes = list(
        Route.objects.filter(pk__in=ids)
        .select_related("driver", "driver__vehicle")
        .prefetch_related(
            "route_orders__order__client",
            "route_orders__order__consignee__zone",
        )
    )
    found = {route.id: route for route in routes}
    sheets = []
    for route_id in ids:
        route = found.get(route_id)
        if route is None:
            continue
        stops = sorted(route.route_orders.all(), key=lambda row: (row.position, row.id))
        weights = order_weights([stop.order for stop in stops])
        sheets.append((route, stops, weights))
    return sheets


def route_sheet_response(sheets) -> HttpResponse:
    if len(sheets) == 1:
        filename = f"route-{sheets[0][0].id}.pdf"
    else:
        filename = "routes.pdf"
    return _pdf(pdfs.routes_sheets_pdf(sheets), filename)


@roles_required("office")
def route_sheet(request, route_id):
    sheets = load_print_routes([route_id])
    if not sheets:
        return HttpResponse("Не найдено", status=404)
    return route_sheet_response(sheets)


@roles_required("office")
def schedule_sheet(request):
    from datetime import date, timedelta

    from django.utils import timezone

    from apps.logistics.schedule import schedule_print_tree

    raw = (request.GET.get("date") or "").strip()
    try:
        day = date.fromisoformat(raw) if raw else timezone.localdate() + timedelta(days=1)
    except ValueError:
        return HttpResponse("Некорректная дата", status=400)
    tree = schedule_print_tree(day)
    return _pdf(pdfs.schedule_sheet_pdf(tree), f"schedule-{day.isoformat()}.pdf")


@roles_required("office")
def printer_test(request, printer_id):
    printer = Printer.objects.filter(pk=printer_id).first()
    target = back_url(request, f"/masters/printers/{printer_id}")
    if printer is None:
        return _fail(request, target, "Принтер не найден")
    try:
        if printer.printer_type == "tsc":
            message = send_tspl(printer, labels.test_label(printer))
        else:
            message = send_pdf(printer, pdfs.test_pdf(), f"test-{printer.id}")
    except WarehouseError as exc:
        return _fail(request, target, str(exc))
    return _ok(request, target, message)
