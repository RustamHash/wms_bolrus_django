from django.shortcuts import redirect

from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, require_int, text
from apps.common.http import done, guarded, roles_required
from apps.planning.models import Task
from apps.planning.services import require_assigned
from apps.shipping.services import ship_pallet


def crossdock_redirect(request):
    return redirect("/tasks?kind=pick")


@roles_required("warehouse", tsd=("ship",))
def task_ship(request, task_id):
    form = request.POST
    back = text(form, "next") or f"/tasks/{task_id}"

    def action():
        task = Task.objects.filter(pk=task_id).first()
        if task is None:
            raise WarehouseError("Задание не найдено")
        require_assigned(task, request.user)
        ship_pallet(
            task_line_id=require_int(form, "task_line_id", "Строка задания"),
            pallet_id=require_int(form, "pallet_id", "Паллета"),
            user=request.user,
            confirmed=form.get("confirm") == "1",
            movement_id=optional_int(form, "movement_id"),
        )

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Паллета отгружена")
