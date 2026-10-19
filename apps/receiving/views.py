from django.shortcuts import redirect

from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, parse_kg, require_int, text
from apps.common.http import done, guarded, roles_required
from apps.receiving.services import accept_loose, accept_pallet, delete_inbound, edit_inbound


def _accept_fields(form):
    manual = form.get("net_manual") == "1"
    return dict(
        packaging_id=require_int(form, "packaging_id", "Упаковка"),
        places=require_int(form, "places", "Количество упаковок"),
        pallet_type=text(form, "pallet_type"),
        gross_kg=parse_kg(form.get("gross"), "Грязный вес"),
        net_kg=parse_kg(form.get("net"), "Чистый вес") if manual else None,
        net_manual=manual,
        caliber_id=require_int(form, "caliber_id", "Калибр"),
        grade_id=require_int(form, "grade_id", "Сортность"),
        reserve=form.get("reserve") == "1",
    )


def receipts_redirect(request, receipt_id=None):
    return redirect("/tasks?kind=receive")


@roles_required("receiver")
def task_receive(request, task_id):
    form = request.POST
    back = text(form, "next") or f"/tasks/{task_id}"

    def action():
        if text(form, "action") == "loose":
            rows = []
            for raw in form.getlist("line"):
                try:
                    line_id = int(raw)
                except ValueError:
                    continue
                qty_raw = (form.get(f"qty-{line_id}") or "").strip()
                if not qty_raw:
                    continue
                rows.append(
                    {
                        "task_line_id": line_id,
                        "caliber_id": require_int(form, f"caliber-{line_id}", "Калибр"),
                        "grade_id": require_int(form, f"grade-{line_id}", "Сортность"),
                        "qty_kg": parse_kg(qty_raw, "Количество"),
                    }
                )
            return accept_loose(task_id=task_id, rows=rows, user=request.user)
        return accept_pallet(
            task_line_id=require_int(form, "task_line_id", "Строка задания"),
            user=request.user,
            client_id=optional_int(form, "client_id"),
            **_accept_fields(form),
        )

    result, response = guarded(request, back, action)
    if response:
        return response
    if text(form, "action") == "loose":
        return done(request, back, "Принято без паллета: " + ", ".join(result))
    return done(request, back, f"Паллета {result[0].number} принята")


@roles_required("receiver")
def movement_edit(request, movement_id):
    form = request.POST
    back = text(form, "next") or "/tasks?kind=receive"

    def action():
        return edit_inbound(movement_id, **_accept_fields(form))

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Приход изменён, остаток пересчитан")


@roles_required("receiver")
def movement_delete(request, movement_id):
    form = request.POST
    back = text(form, "next") or "/tasks?kind=receive"

    def action():
        delete_inbound(movement_id)

    result, response = guarded(request, back, action)
    if response:
        return response
    return done(request, back, "Ошибочный приход удалён, остаток возвращён")
