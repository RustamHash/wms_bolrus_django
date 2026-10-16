from django.http import Http404
from django.shortcuts import render

from apps.catalog.services import load_refs
from apps.common.constants import TASK_WORK_STATUS
from apps.common.http import roles_required
from apps.inventory.models import Movement
from apps.planning.models import Task
from apps.planning.views import task_context
from apps.storage.services import find_by_barcode


def _tasks(kind: str):
    return Task.objects.filter(kind=kind, status=TASK_WORK_STATUS[kind]).order_by("-id")


@roles_required("receiver", "warehouse")
def tsd_menu(request):
    return render(request, "tsd/menu.html")


@roles_required("receiver")
def tsd_receive(request):
    return render(request, "tsd/tasks.html", {"title": "Приёмка", "tasks": _tasks("receive")})


@roles_required("warehouse")
def tsd_pick(request):
    return render(request, "tsd/tasks.html", {"title": "Отбор", "tasks": _tasks("pick")})


@roles_required("warehouse")
def tsd_ship(request):
    return render(request, "tsd/tasks.html", {"title": "На отгрузку", "tasks": _tasks("ship")})


@roles_required("receiver", "warehouse")
def tsd_task(request, task_id):
    task = Task.objects.select_related("supplier", "purchase_order", "order").filter(pk=task_id).first()
    if task is None:
        raise Http404
    movements = list(
        Movement.objects.filter(task=task, kind="in")
        .select_related(
            "pallet__order__client",
            "pallet__reserved_client",
            "pallet__reserved_line__order",
            "lot__product",
            "lot__caliber",
            "lot__grade",
        )
        .prefetch_related("pallet__units__packaging")
        .order_by("-id")
    )
    return render(request, "tsd/task.html", task_context(task, movements))


@roles_required("receiver", "warehouse")
def tsd_find(request):
    code = request.GET.get("barcode") or ""
    kind, obj = find_by_barcode(code) if code else (None, None)
    movement = None
    if kind == "pallet":
        movement = Movement.objects.filter(pallet_id=obj.id, kind="in").first()
    elif kind == "unit" and obj.pallet_id:
        movement = Movement.objects.filter(pallet_id=obj.pallet_id, kind="in").first()
    return render(request, "tsd/find.html", {"barcode": code, "kind": kind, "obj": obj, "movement": movement, **load_refs()})
