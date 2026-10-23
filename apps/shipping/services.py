from django.db import transaction

from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.inventory.models import Movement
from apps.inventory.services import physical_left, post_out
from apps.planning.models import TaskLine
from apps.planning.spec import same_spec, specificity
from apps.storage.models import HandlingUnit, Pallet


def _same_spec(movement, line: TaskLine) -> bool:
    return same_spec(movement.lot, line)


def shipped_units(task) -> list:
    pallet_ids = Movement.objects.filter(task_id=task.id, kind="out").values_list("pallet_id", flat=True)
    return list(
        HandlingUnit.objects.filter(pallet_id__in=pallet_ids)
        .select_related("lot__product", "lot__caliber", "grade", "packaging")
        .order_by("id")
    )


def _unshipped_pick(pallet_id: int, order_id: int, movement_id: int | None):
    shipped = Movement.objects.filter(kind="out").exclude(source_id=None).values("source_id")
    query = (
        Movement.objects.filter(kind="pick", pallet_id=pallet_id, task__order_id=order_id)
        .exclude(id__in=shipped)
        .select_related("task", "lot")
    )
    if movement_id:
        return query.filter(pk=movement_id).first()
    return query.order_by("id").first()


@transaction.atomic
def ship_pallet(*, task_line_id: int, pallet_id: int, user, confirmed: bool, movement_id: int | None = None):
    if not confirmed:
        raise WarehouseError("Подтвердите условия отгрузки")
    line = get_row(TaskLine, task_line_id, "строка задания")
    task = line.task
    if task.kind != "ship" or not task.is_working():
        raise WarehouseError("Нет открытого задания на отгрузку")
    pallet = get_row(Pallet, pallet_id, "паллета")
    source = _unshipped_pick(pallet.id, task.order_id, movement_id)
    if source is None:
        raise WarehouseError("Паллета ещё не отобрана")
    if not _same_spec(source, line):
        raise WarehouseError("Паллета не совпадает со строкой задания")
    movement = post_out(task=task, task_line=line, pallet=pallet, source=source, user=user)
    if physical_left(pallet.id) <= 0:
        for unit in pallet.units.all():
            unit.status = "shipped"
            unit.save(update_fields=["status"])
    return movement


def waiting_pallets(task) -> list[dict]:
    shipped = Movement.objects.filter(kind="out").exclude(source_id=None).values("source_id")
    rows = (
        Movement.objects.filter(kind="pick", task__order_id=task.order_id)
        .exclude(id__in=shipped)
        .select_related("task", "pallet", "lot__product", "lot__caliber", "lot__grade")
    )
    found = []
    lines = sorted(
        task.lines.select_related(
            "order_line__order__consignee",
            "order_line__caliber_from",
            "order_line__caliber_to",
            "caliber",
            "grade",
        ).prefetch_related(
            "order_line__grades",
            "order_line__order__consignee__allowed_grades",
            "order_line__order__consignee__category__allowed_grades",
        ),
        key=specificity,
    )
    for movement in rows:
        for line in lines:
            if _same_spec(movement, line):
                found.append({"line": line, "movement": movement})
                break
    return found
