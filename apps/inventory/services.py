from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.common.exceptions import WarehouseError
from apps.common.kg import as_kg
from apps.inventory.models import Balance, Movement


def _lock_balance(lot_id: int) -> Balance:
    row = Balance.objects.select_for_update().filter(lot_id=lot_id).first()
    if row is None:
        row = Balance.objects.create(
            lot_id=lot_id,
            free_kg=Decimal("0.000"),
            reserved_kg=Decimal("0.000"),
            picked_kg=Decimal("0.000"),
        )
    return row


def inbound_is_reserved(movement) -> bool:
    pallet = movement.pallet
    return bool(
        pallet.reserved_line_id
        or pallet.reserved_client_id
        or (pallet.kind == "inbound" and pallet.order_id)
    )


def _apply(row: Balance, free_delta: Decimal, picked_delta: Decimal, reserved_delta: Decimal = Decimal("0")) -> None:
    row.free_kg = as_kg(row.free_kg) + as_kg(free_delta)
    row.reserved_kg = as_kg(row.reserved_kg) + as_kg(reserved_delta)
    row.picked_kg = as_kg(row.picked_kg) + as_kg(picked_delta)
    if row.free_kg < 0 or row.reserved_kg < 0 or row.picked_kg < 0:
        raise WarehouseError("Недостаточно остатка")
    row.save(update_fields=["free_kg", "reserved_kg", "picked_kg"])


def has_downstream(movement) -> bool:
    return Movement.objects.filter(pallet_id=movement.pallet_id, kind__in=("pick", "out")).exists()


def inbound_for_pallet(pallet_id: int):
    return (
        Movement.objects.filter(pallet_id=pallet_id, kind="in")
        .select_related("task", "lot", "pallet__reserved_line")
        .first()
    )


def pick_for_pallet(pallet_id: int):
    return Movement.objects.filter(pallet_id=pallet_id, kind="pick").select_related("task", "lot").first()


def received_kg(line) -> Decimal:
    total = Movement.objects.filter(task_line_id=line.id, kind="in").aggregate(total=Sum("qty_kg"))["total"]
    return as_kg(total or 0)


def kg_by(field: str, kind: str) -> dict[int, Decimal]:
    lookup = f"task_line__{field}"
    rows = (
        Movement.objects.filter(kind=kind)
        .exclude(**{f"{lookup}__isnull": True})
        .values(lookup)
        .annotate(total=Sum("qty_kg"))
    )
    return {row[lookup]: as_kg(row["total"]) for row in rows}


@transaction.atomic
def post_in(*, task, task_line, pallet, lot, qty_kg: Decimal, user, reserved: bool = False) -> Movement:
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес паллеты должен быть больше нуля")
    movement = Movement.objects.create(
        task=task,
        task_line=task_line,
        pallet=pallet,
        kind="in",
        lot=lot,
        qty_kg=qty,
        created_by=user,
    )
    if reserved:
        _apply(_lock_balance(lot.id), Decimal("0"), Decimal("0"), qty)
    else:
        _apply(_lock_balance(lot.id), qty, Decimal("0"))
    return movement


@transaction.atomic
def update_in(movement, *, lot, qty_kg: Decimal) -> Movement:
    if movement.kind != "in":
        raise WarehouseError("Правится только приход")
    if has_downstream(movement):
        raise WarehouseError("По паллете уже есть отбор или отгрузка")
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес паллеты должен быть больше нуля")
    reserved = inbound_is_reserved(movement)
    old = as_kg(movement.qty_kg)
    if reserved:
        _apply(_lock_balance(movement.lot_id), Decimal("0"), Decimal("0"), -old)
    else:
        _apply(_lock_balance(movement.lot_id), -old, Decimal("0"))
    movement.lot = lot
    movement.qty_kg = qty
    movement.save(update_fields=["lot", "qty_kg"])
    if reserved:
        _apply(_lock_balance(lot.id), Decimal("0"), Decimal("0"), qty)
    else:
        _apply(_lock_balance(lot.id), qty, Decimal("0"))
    return movement


@transaction.atomic
def remove_in(movement) -> None:
    if movement.kind != "in":
        raise WarehouseError("Удаляется только приход")
    if has_downstream(movement):
        raise WarehouseError("По паллете уже есть отбор или отгрузка")
    qty = as_kg(movement.qty_kg)
    if inbound_is_reserved(movement):
        _apply(_lock_balance(movement.lot_id), Decimal("0"), Decimal("0"), -qty)
    else:
        _apply(_lock_balance(movement.lot_id), -qty, Decimal("0"))
    movement.delete()


@transaction.atomic
def post_pick(*, task, task_line, pallet, source: Movement, user) -> Movement:
    if source.kind != "in" or source.pallet_id != pallet.id:
        raise WarehouseError("Отбирать можно только принятую паллету")
    if has_downstream(source):
        raise WarehouseError("Паллета уже отобрана или отгружена")
    qty = as_kg(source.qty_kg)
    movement = Movement.objects.create(
        task=task,
        task_line=task_line,
        pallet=pallet,
        kind="pick",
        lot_id=source.lot_id,
        qty_kg=qty,
        source=source,
        created_by=user,
    )
    if inbound_is_reserved(source):
        _apply(_lock_balance(source.lot_id), Decimal("0"), qty, -qty)
    else:
        _apply(_lock_balance(source.lot_id), -qty, qty)
    return movement


@transaction.atomic
def post_out(*, task, task_line, pallet, source: Movement, user) -> Movement:
    if source.kind != "pick" or source.pallet_id != pallet.id:
        raise WarehouseError("Отгружается только отобранная паллета")
    if Movement.objects.filter(pallet_id=pallet.id, kind="out").exists():
        raise WarehouseError("Паллета уже отгружена")
    qty = as_kg(source.qty_kg)
    movement = Movement.objects.create(
        task=task,
        task_line=task_line,
        pallet=pallet,
        kind="out",
        lot_id=source.lot_id,
        qty_kg=qty,
        source=source,
        created_by=user,
    )
    _apply(_lock_balance(source.lot_id), Decimal("0"), -qty)
    return movement


def stock_rows() -> list[dict]:
    rows = Balance.objects.select_related(
        "lot__product", "lot__caliber", "lot__grade", "lot__supplier", "lot__receipt"
    ).order_by("lot__received_on", "id")
    result = []
    for row in rows:
        if as_kg(row.free_kg) == 0 and as_kg(row.reserved_kg) == 0 and as_kg(row.picked_kg) == 0:
            continue
        lot = row.lot
        result.append(
            {
                "product": lot.product.name,
                "caliber": lot.caliber.name,
                "grade": lot.grade.name,
                "supplier": lot.supplier.name,
                "receipt": lot.receipt.number or "",
                "free_kg": as_kg(row.free_kg),
                "reserved_kg": as_kg(row.reserved_kg),
                "picked_kg": as_kg(row.picked_kg),
            }
        )
    return result
