from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.common.forms import query_date, require_int, text
from apps.common.http import done, guarded, roles_required
from apps.shipping.models import Shipment
from apps.shipping.services import ship_pallet


def crossdock_redirect(request):
    return redirect("/tasks?kind=pick")


@roles_required("warehouse")
def shipment_list(request):
    date_from = query_date(request.GET.get("date_from"))
    date_to = query_date(request.GET.get("date_to"))
    party = (request.GET.get("party") or "").strip()
    number = (request.GET.get("number") or "").strip()
    query = Shipment.objects.select_related("order").order_by("-id")
    if date_from:
        query = query.filter(created_at__date__gte=date_from)
    if date_to:
        query = query.filter(created_at__date__lte=date_to)
    if party:
        query = query.filter(Q(client_name__icontains=party) | Q(seller_name__icontains=party))
    if number:
        query = query.filter(Q(number__icontains=number) | Q(order__number__icontains=number))
    return render(
        request,
        "shipments/list.html",
        {
            "shipments": query,
            "date_from": request.GET.get("date_from") or "",
            "date_to": request.GET.get("date_to") or "",
            "party": party,
            "number": number,
        },
    )


@roles_required("warehouse")
def shipment_detail(request, shipment_id):
    shipment = get_object_or_404(
        Shipment.objects.select_related("order").prefetch_related("units__lot__product", "units__lot__caliber", "units__grade", "units__packaging"),
        pk=shipment_id,
    )
    return render(request, "shipments/detail.html", {"shipment": shipment})


@roles_required("warehouse", tsd=("ship",))
def task_ship(request, task_id):
    form = request.POST
    back = text(form, "next") or f"/tasks/{task_id}"

    def action():
        return ship_pallet(
            task_line_id=require_int(form, "task_line_id", "Строка задания"),
            pallet_id=require_int(form, "pallet_id", "Паллета"),
            user=request.user,
            confirmed=form.get("confirm") == "1",
        )

    result, response = guarded(request, back, action)
    if response:
        return response
    _movement, shipment = result
    if text(form, "next"):
        return done(request, text(form, "next"), "Паллета отгружена")
    return done(request, f"/shipments/{shipment.id}", "Паллета проведена в накладную")
