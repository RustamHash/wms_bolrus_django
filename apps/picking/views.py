from apps.common.forms import require_int, text
from apps.common.http import done, guarded, roles_required
from apps.picking.services import pick_pallet


@roles_required("warehouse", tsd=("pick",))
def task_pick(request, task_id):
    form = request.POST
    back = text(form, "next") or f"/tasks/{task_id}"

    def action():
        return pick_pallet(
            task_line_id=require_int(form, "task_line_id", "Строка задания"),
            pallet_id=require_int(form, "pallet_id", "Паллета"),
            user=request.user,
        )

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, f"Паллета {result.pallet.number} отобрана")
