from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.catalog.grade_policy import allowed_grade_ids
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.kg import as_kg, format_kg
from apps.inventory.models import Movement
from apps.inventory.services import (
    consumed_pallet_ids,
    inbound_for_pallet,
    pallet_room,
    post_pick,
    unpicked_kg,
)
from apps.picking.models import PickAllocation
from apps.planning.models import Task, TaskLine
from apps.planning.spec import same_spec, specificity
from apps.storage.categories import allows_consignee


def _same_spec(movement, line: TaskLine) -> bool:
    return same_spec(movement.lot, line)


def _picked_kg(line_id: int) -> Decimal:
    total = Movement.objects.filter(task_line_id=line_id, kind="pick").aggregate(total=Sum("qty_kg"))["total"]
    return as_kg(total or 0)


def _picked_on(line_id: int, pallet_id: int) -> Decimal:
    total = Movement.objects.filter(task_line_id=line_id, pallet_id=pallet_id, kind="pick").aggregate(total=Sum("qty_kg"))[
        "total"
    ]
    return as_kg(total or 0)


def picked_map(task) -> dict[int, Decimal]:
    rows = (
        Movement.objects.filter(task_line__task=task, kind="pick")
        .values("task_line_id")
        .annotate(total=Sum("qty_kg"))
    )
    return {row["task_line_id"]: as_kg(row["total"] or 0) for row in rows}


def _planned_kg(line_id: int) -> Decimal:
    total = PickAllocation.objects.filter(task_line_id=line_id).aggregate(total=Sum("qty_kg"))["total"]
    return as_kg(total or 0)


def _covered(line_id: int, fact: Decimal) -> Decimal:
    planned = _planned_kg(line_id)
    return fact if fact >= planned else planned


def allocation_rows(task) -> list[dict]:
    rows = (
        PickAllocation.objects.filter(task_line__task=task)
        .select_related("pallet", "task_line__product", "task_line__caliber", "task_line__grade")
        .order_by("task_line_id", "id")
    )
    result = []
    for alloc in rows:
        fact = _picked_on(alloc.task_line_id, alloc.pallet_id)
        left = as_kg(alloc.qty_kg) - fact
        if left < 0:
            left = Decimal("0.000")
        result.append({"allocation": alloc, "fact": fact, "left": left})
    return result


def _fits_task(movement, line, task) -> bool:
    if not _same_spec(movement, line):
        return False
    consignee = task.order.consignee if task.order_id else None
    if not allows_consignee(movement.pallet, consignee):
        return False
    allowed = allowed_grade_ids(consignee)
    return allowed is None or movement.lot.grade_id in allowed


def _pallet_allowed(pallet, line, task) -> bool:
    if pallet.reserved_line_id and pallet.reserved_line_id != line.order_line_id:
        return False
    if pallet.reserved_line_id:
        return True
    if pallet.reserved_consignee_id and task.order_id and pallet.reserved_consignee_id != task.order.consignee_id:
        return False
    if pallet.order_id and pallet.order_id != task.order_id:
        return False
    return True


def _open_pick(task):
    if task.kind != "pick" or not task.is_working():
        raise WarehouseError("Нет открытого задания на отбор")
    if not task.order_id:
        raise WarehouseError("У задания нет заказа")
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
    if not lines or any(line.order_line_id is None for line in lines):
        raise WarehouseError("В задании нет строк заказа")
    return lines


def _stock_rows():
    return (
        Movement.objects.filter(kind="in")
        .exclude(pallet_id__in=consumed_pallet_ids())
        .select_related("lot__product", "lot__caliber", "lot__grade", "pallet")
        .prefetch_related("pallet__allowed_categories")
        .order_by("lot__received_on", "id")
    )


def release_unpicked_plan(task) -> None:
    """Снимает с паллет килограммы, которые в закрытом задании так и не отобрали."""
    for alloc in PickAllocation.objects.select_for_update(of=("self",)).filter(task_line__task=task):
        fact = _picked_on(alloc.task_line_id, alloc.pallet_id)
        if fact <= 0:
            alloc.delete()
        elif as_kg(alloc.qty_kg) > fact:
            alloc.qty_kg = fact
            alloc.save(update_fields=["qty_kg"])


def _sync_order(task) -> None:
    if not task.order_id:
        return
    from apps.planning.services import sync_order_pick_status

    sync_order_pick_status(task.order)


def refresh_pick_status(task) -> None:
    task = Task.objects.select_for_update().get(pk=task.pk)
    if task.kind != "pick" or task.status == "closed":
        return
    lines = list(task.lines.all())
    if not lines:
        return
    facts = {line.id: _picked_kg(line.id) for line in lines}
    all_done = all(facts[line.id] >= as_kg(line.qty_kg) for line in lines)
    any_fact = any(facts[line.id] > 0 for line in lines)
    planned_rows = (
        PickAllocation.objects.filter(task_line__task_id=task.pk)
        .values("task_line_id")
        .annotate(total=Sum("qty_kg"))
    )
    planned = {row["task_line_id"]: as_kg(row["total"]) for row in planned_rows}
    short = any(planned.get(line.id, Decimal("0")) < as_kg(line.qty_kg) for line in lines)
    if all_done:
        new = "picked"
    elif any_fact:
        new = "in_work"
    elif planned and short:
        new = "partial"
    elif planned:
        new = "planned"
    else:
        new = "created"
    if new == "picked":
        release_unpicked_plan(task)
    if task.status != new:
        task.status = new
        task.save(update_fields=["status"])
    if new == "picked":
        _sync_order(task)


def assert_pick_closable(task) -> None:
    gap = Decimal("0.000")
    for line in task.lines.all():
        left = as_kg(line.qty_kg) - _picked_kg(line.id)
        if left > 0:
            gap += left
    if gap > 0:
        raise WarehouseError(f"Нельзя закрыть отбор: не хватает {format_kg(gap)} кг")


def _mark_allocated(pallet) -> None:
    if unpicked_kg(pallet.id) > 0:
        return
    pallet.units.exclude(status__in=("allocated", "shipped", "transformed", "rejected")).update(status="allocated")


@transaction.atomic
def pick_allocation(*, allocation_id: int, user, qty_kg: Decimal | None = None) -> Movement:
    alloc = (
        PickAllocation.objects.select_for_update(of=("self",))
        .select_related("task_line__task__order", "pallet")
        .filter(pk=allocation_id)
        .first()
    )
    if alloc is None:
        raise WarehouseError("Назначение не найдено")
    line = alloc.task_line
    task = line.task
    if task.kind != "pick" or not task.is_working():
        raise WarehouseError("Нет открытого задания на отбор")
    fact = _picked_on(line.id, alloc.pallet_id)
    left = as_kg(alloc.qty_kg) - fact
    if left <= 0:
        raise WarehouseError("Это назначение уже отобрано")
    qty = left if qty_kg is None else as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес отбора должен быть больше нуля")
    if qty > left:
        raise WarehouseError(f"Больше плана назначения. Осталось {format_kg(left)} кг")
    line_left = as_kg(line.qty_kg) - _picked_kg(line.id)
    if qty > line_left:
        raise WarehouseError(f"Больше плана строки. Осталось {format_kg(line_left)} кг")
    source = inbound_for_pallet(alloc.pallet_id)
    if source is None:
        raise WarehouseError("Паллета не принята")
    movement = post_pick(
        task=task,
        task_line=line,
        pallet=alloc.pallet,
        source=source,
        user=user,
        qty_kg=qty,
    )
    _mark_allocated(alloc.pallet)
    refresh_pick_status(task)
    return movement


@transaction.atomic
def reserve_for_task(task) -> str:
    """Назначает килограммы паллет на строки задания. Целую паллету не закрепляет."""
    lines = _open_pick(task)
    for alloc in PickAllocation.objects.select_for_update(of=("self",)).filter(task_line__task=task):
        if _picked_on(alloc.task_line_id, alloc.pallet_id) <= 0:
            alloc.delete()
    candidates = list(_stock_rows())
    rooms: dict[int, Decimal] = {}

    def room(pallet_id: int) -> Decimal:
        if pallet_id not in rooms:
            have = pallet_room(pallet_id, exclude_task_id=task.id)
            for kept in PickAllocation.objects.filter(task_line__task=task, pallet_id=pallet_id):
                left = as_kg(kept.qty_kg) - _picked_on(kept.task_line_id, pallet_id)
                if left > 0:
                    have = as_kg(have - left)
            rooms[pallet_id] = have if have > 0 else Decimal("0.000")
        return rooms[pallet_id]

    created = 0
    planned_kg = Decimal("0.000")
    short = Decimal("0.000")
    for line in lines:
        fact = _picked_kg(line.id)
        unpicked_kept = Decimal("0.000")
        for alloc in PickAllocation.objects.filter(task_line=line):
            got = _picked_on(line.id, alloc.pallet_id)
            left = as_kg(alloc.qty_kg) - got
            if left > 0:
                unpicked_kept += left
        need = as_kg(line.qty_kg) - fact - unpicked_kept
        if need < 0:
            need = Decimal("0.000")
        ordered = sorted(
            candidates,
            key=lambda movement: (
                0 if movement.pallet.reserved_line_id == line.order_line_id else 1,
                movement.lot.received_on or date.min,
                movement.id,
            ),
        )
        for movement in ordered:
            if need <= 0:
                break
            pallet = movement.pallet
            if not _pallet_allowed(pallet, line, task) or not _fits_task(movement, line, task):
                continue
            have = room(pallet.id)
            if have <= 0:
                continue
            take = have if have < need else need
            existing = PickAllocation.objects.filter(task_line=line, pallet=pallet).first()
            if existing is None:
                PickAllocation.objects.create(task_line=line, pallet=pallet, qty_kg=take)
            else:
                existing.qty_kg = as_kg(existing.qty_kg) + take
                existing.save(update_fields=["qty_kg"])
            rooms[pallet.id] = as_kg(have - take)
            need = as_kg(need - take)
            planned_kg += take
            created += 1
        if need > 0:
            short += need
    refresh_pick_status(task)
    if created == 0 and planned_kg == 0:
        covered = all(_covered(line.id, _picked_kg(line.id)) >= as_kg(line.qty_kg) for line in lines)
        if covered:
            return "уже в плане"
        raise WarehouseError("Нет паллет под задание")
    text = f"{created} назн., {format_kg(planned_kg)} кг"
    if short > 0:
        text += f", не хватило {format_kg(short)} кг"
    return text


def pick_ready_task(task, user) -> str:
    """Проводит оставшийся план по назначениям."""
    _open_pick(task)
    ready = [item for item in allocation_rows(task) if item["left"] > 0]
    if not ready:
        raise WarehouseError("Нет назначений к отбору")
    picked = 0
    kg = Decimal("0.000")
    failed = []
    for item in ready:
        alloc = item["allocation"]
        try:
            movement = pick_allocation(allocation_id=alloc.id, user=user)
            picked += 1
            kg += as_kg(movement.qty_kg)
        except WarehouseError as exc:
            number = alloc.pallet.number or alloc.pallet_id
            failed.append(f"{number}: {exc}")
    if picked == 0:
        raise WarehouseError("; ".join(failed) or "Ничего не отобрано")
    text = f"{picked} назн., {format_kg(kg)} кг"
    if failed:
        text += ", не отобрано: " + "; ".join(failed)
    return text
