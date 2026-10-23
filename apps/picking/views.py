from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, parse_kg, text
from apps.common.http import done, guarded, roles_required
from apps.common.kg import format_kg
from apps.picking.services import pick_allocation
from apps.planning.models import Task
from apps.planning.services import require_assigned


@roles_required("warehouse", tsd=("pick",))
def task_pick(request, task_id):
    form = request.POST
    back = text(form, "next") or f"/tasks/{task_id}"

    def action():
        task = Task.objects.filter(pk=task_id).first()
        if task is None:
            raise WarehouseError("Задание не найдено")
        require_assigned(task, request.user)
        raw = text(form, "qty_kg")
        qty = parse_kg(raw, "Кг") if raw else None
        allocation_id = optional_int(form, "allocation_id")
        if not allocation_id:
            raise WarehouseError("Нет назначения отбора")
        return pick_allocation(allocation_id=allocation_id, user=request.user, qty_kg=qty)

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, f"Отобрано {format_kg(result.qty_kg)} кг")
