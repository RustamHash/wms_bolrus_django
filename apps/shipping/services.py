from django.db import transaction

from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.numbers import document_number
from apps.inventory.models import Movement
from apps.inventory.services import pick_for_pallet, post_out
from apps.planning.models import TaskLine
from apps.planning.spec import same_spec, specificity
from apps.shipping.models import Shipment, ShipmentUnit
from apps.storage.models import Pallet


def _same_spec(movement, line: TaskLine) -> bool:
    return same_spec(movement.lot, line)


def _shipment(task, user) -> Shipment:
    shipment = Shipment.objects.filter(task_id=task.id).first()
    if shipment is not None:
        return shipment
    order = task.order
    shipment = Shipment.objects.create(
        order=order,
        task=task,
        seller_name=order.seller.name,
        seller_requisites=order.seller.requisites,
        client_name=order.client.name,
        shipping_terms=order.shipping_terms,
        created_by=user,
    )
    shipment.number = document_number("ОТ", shipment.id)
    shipment.save(update_fields=["number"])
    return shipment


@transaction.atomic
def ship_pallet(*, task_line_id: int, pallet_id: int, user, confirmed: bool):
    if not confirmed:
        raise WarehouseError("Подтвердите условия отгрузки")
    line = get_row(TaskLine, task_line_id, "строка задания")
    task = line.task
    if task.kind != "ship" or not task.is_working():
        raise WarehouseError("Нет открытого задания на отгрузку")
    pallet = get_row(Pallet, pallet_id, "паллета")
    source = pick_for_pallet(pallet.id)
    if source is None:
        raise WarehouseError("Паллета ещё не отобрана")
    if source.task.order_id != task.order_id:
        raise WarehouseError("Паллета отобрана по другому заказу")
    if not _same_spec(source, line):
        raise WarehouseError("Паллета не совпадает со строкой задания")
    movement = post_out(task=task, task_line=line, pallet=pallet, source=source, user=user)
    shipment = _shipment(task, user)
    for unit in pallet.units.all():
        unit.status = "shipped"
        unit.save(update_fields=["status"])
        ShipmentUnit.objects.create(shipment=shipment, unit=unit)
    return movement, shipment


def waiting_pallets(task) -> list[dict]:
    rows = Movement.objects.filter(kind="pick").select_related(
        "task", "pallet", "lot__product", "lot__caliber", "lot__grade"
    )
    found = []
    lines = sorted(
        task.lines.select_related(
            "order_line__order__consignee",
            "order_line__caliber_from",
            "order_line__caliber_to",
            "caliber",
            "grade",
        ).prefetch_related("order_line__grades", "order_line__order__consignee__allowed_grades"),
        key=specificity,
    )
    for movement in rows:
        shipped = Movement.objects.filter(pallet_id=movement.pallet_id, kind="out").exists()
        if shipped or movement.task.order_id != task.order_id:
            continue
        for line in lines:
            if _same_spec(movement, line):
                found.append({"line": line, "movement": movement})
                break
    return found
