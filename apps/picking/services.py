from django.db import transaction

from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.inventory.models import Movement
from apps.inventory.services import has_downstream, inbound_for_pallet, post_pick
from apps.catalog.grade_policy import allowed_grade_ids
from apps.planning.models import TaskLine
from apps.storage.models import Pallet


def _same_spec(movement, line: TaskLine) -> bool:
    lot = movement.lot
    return (
        lot.product_id == line.product_id
        and lot.caliber_id == line.caliber_id
        and lot.grade_id == line.grade_id
    )


@transaction.atomic
def pick_pallet(*, task_line_id: int, pallet_id: int, user):
    line = get_row(TaskLine, task_line_id, "строка задания")
    task = line.task
    if task.kind != "pick" or not task.is_working():
        raise WarehouseError("Нет открытого задания на отбор")
    if task.order_id != line.order_line.order_id:
        raise WarehouseError("Строка задания не из этого заказа")
    pallet = get_row(Pallet, pallet_id, "паллета")
    source = inbound_for_pallet(pallet.id)
    if source is None:
        raise WarehouseError("Паллета не принята")
    owner = source.pallet.reserved_line.order_id if source.pallet.reserved_line_id else source.pallet.order_id
    if owner and owner != task.order_id:
        raise WarehouseError("Паллета в резерве под другой заказ")
    if source.pallet.reserved_line_id and line.order_line_id and line.order_line_id != source.pallet.reserved_line_id:
        raise WarehouseError("Паллета в резерве под другую строку заказа")
    if source.pallet.reserved_client_id:
        client_id = task.order.client_id if task.order_id else None
        if client_id != source.pallet.reserved_client_id:
            raise WarehouseError(f"Паллета в резерве под клиента {source.pallet.reserved_client.name}")
    if not _same_spec(source, line):
        raise WarehouseError("Паллета не совпадает со строкой задания")
    consignee = task.order.consignee if task.order_id else None
    allowed = allowed_grade_ids(consignee)
    if allowed is not None and source.lot.grade_id not in allowed:
        raise WarehouseError(f"Грузополучателю {consignee.name} нельзя сортность {source.lot.grade.name}")
    movement = post_pick(task=task, task_line=line, pallet=pallet, source=source, user=user)
    for unit in pallet.units.all():
        unit.status = "allocated"
        unit.save(update_fields=["status"])
    return movement


def waiting_pallets(task) -> list[dict]:
    rows = Movement.objects.filter(kind="in").select_related(
        "task", "pallet__reserved_line", "pallet__reserved_client", "pallet__order", "task_line", "lot__grade"
    )
    found = []
    lines = list(task.lines.all())
    consignee = task.order.consignee if task.order_id else None
    allowed = allowed_grade_ids(consignee)
    for movement in rows:
        if has_downstream(movement):
            continue
        if allowed is not None and movement.lot.grade_id not in allowed:
            continue
        pallet = movement.pallet
        owner = pallet.reserved_line.order_id if pallet.reserved_line_id else pallet.order_id
        if owner and owner != task.order_id:
            continue
        if pallet.reserved_client_id:
            client_id = task.order.client_id if task.order_id else None
            if client_id != pallet.reserved_client_id:
                continue
        if pallet.reserved_line_id:
            match = next((line for line in lines if line.order_line_id == pallet.reserved_line_id and _same_spec(movement, line)), None)
            if match is None:
                continue
            found.append({"line": match, "movement": movement})
            continue
        for line in lines:
            if _same_spec(movement, line):
                found.append({"line": line, "movement": movement})
                break
    return found
