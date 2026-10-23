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
        or pallet.reserved_consignee_id
        or (pallet.kind == "inbound" and pallet.order_id)
    )


def reserve_free_inbound(movement) -> None:
    """Переносит вес принятой паллеты из свободного остатка в резерв. Паллета уже отмечена резервом."""
    if movement.kind != "in":
        raise WarehouseError("Резерв ставится на принятую паллету")
    if has_left(movement):
        raise WarehouseError("Паллета уже отобрана, отгружена или списана в переработку")
    if not inbound_is_reserved(movement):
        raise WarehouseError("Паллета не отмечена резервом")
    qty = as_kg(movement.qty_kg)
    _apply(_lock_balance(movement.lot_id), -qty, Decimal("0"), qty)


def _apply(row: Balance, free_delta: Decimal, picked_delta: Decimal, reserved_delta: Decimal = Decimal("0")) -> None:
    row.free_kg = as_kg(row.free_kg) + as_kg(free_delta)
    row.reserved_kg = as_kg(row.reserved_kg) + as_kg(reserved_delta)
    row.picked_kg = as_kg(row.picked_kg) + as_kg(picked_delta)
    if row.free_kg < 0 or row.reserved_kg < 0 or row.picked_kg < 0:
        raise WarehouseError("Недостаточно остатка")
    row.save(update_fields=["free_kg", "reserved_kg", "picked_kg"])


LEFT_KINDS = (
    Movement.Kind.PICK,
    Movement.Kind.OUT,
    Movement.Kind.TRANSFORM_OUT,
    Movement.Kind.WASTE,
)
TAKEN_KINDS = (
    Movement.Kind.PICK,
    Movement.Kind.TRANSFORM_OUT,
    Movement.Kind.WASTE,
)


def _kg_by_pallet(kinds, pallet_ids=None) -> dict[int, Decimal]:
    query = Movement.objects.filter(kind__in=kinds)
    if pallet_ids is not None:
        query = query.filter(pallet_id__in=list(pallet_ids))
    rows = query.values("pallet_id").annotate(total=Sum("qty_kg"))
    return {row["pallet_id"]: as_kg(row["total"] or 0) for row in rows}


def inbound_kg_by_pallet(pallet_ids=None) -> dict[int, Decimal]:
    return _kg_by_pallet((Movement.Kind.IN,), pallet_ids)


def taken_kg_by_pallet(pallet_ids=None) -> dict[int, Decimal]:
    """Вес, который уже нельзя отобрать: отбор, переработка и отход."""
    return _kg_by_pallet(TAKEN_KINDS, pallet_ids)


def shipped_kg_by_pallet(pallet_ids=None) -> dict[int, Decimal]:
    return _kg_by_pallet((Movement.Kind.OUT,), pallet_ids)


def open_plan_kg_by_pallet(pallet_ids=None, exclude_task_id: int | None = None) -> dict[int, Decimal]:
    """Неотобранный план назначений. Отбор этого задания при exclude_task_id не вычитается."""
    from apps.picking.models import PickAllocation

    query = PickAllocation.objects.all()
    if pallet_ids is not None:
        query = query.filter(pallet_id__in=list(pallet_ids))
    if exclude_task_id is not None:
        query = query.exclude(task_line__task_id=exclude_task_id)
    allocs = list(query.values("pallet_id", "task_line_id", "qty_kg"))
    if not allocs:
        return {}
    line_ids = {row["task_line_id"] for row in allocs}
    pallets = {row["pallet_id"] for row in allocs}
    picks = (
        Movement.objects.filter(kind=Movement.Kind.PICK, task_line_id__in=line_ids, pallet_id__in=pallets)
        .values("task_line_id", "pallet_id")
        .annotate(total=Sum("qty_kg"))
    )
    picked = {(row["task_line_id"], row["pallet_id"]): as_kg(row["total"] or 0) for row in picks}
    found: dict[int, Decimal] = {}
    for row in allocs:
        left = as_kg(row["qty_kg"]) - picked.get((row["task_line_id"], row["pallet_id"]), Decimal("0.000"))
        if left <= 0:
            continue
        found[row["pallet_id"]] = found.get(row["pallet_id"], Decimal("0.000")) + left
    return found


def available_kg_by_pallet(pallet_ids=None) -> dict[int, Decimal]:
    """Приход минус отбор, переработка и ещё не отобранный план."""
    inbound = inbound_kg_by_pallet(pallet_ids)
    taken = taken_kg_by_pallet(list(inbound) or pallet_ids)
    planned = open_plan_kg_by_pallet(list(inbound) or pallet_ids)
    found = {}
    for pallet_id, qty in inbound.items():
        left = as_kg(qty - taken.get(pallet_id, 0) - planned.get(pallet_id, 0))
        found[pallet_id] = left if left > 0 else Decimal("0.000")
    return found


def pallet_room(pallet_id: int, *, exclude_task_id: int | None = None) -> Decimal:
    """Сколько ещё можно отобрать, не занимая план других заданий."""
    inbound = inbound_kg_by_pallet([pallet_id]).get(pallet_id, Decimal("0.000"))
    taken = taken_kg_by_pallet([pallet_id]).get(pallet_id, Decimal("0.000"))
    others = open_plan_kg_by_pallet([pallet_id], exclude_task_id=exclude_task_id).get(pallet_id, Decimal("0.000"))
    left = as_kg(inbound - taken - others)
    return left if left > 0 else Decimal("0.000")


def unpicked_kg(pallet_id: int) -> Decimal:
    inbound = inbound_kg_by_pallet([pallet_id]).get(pallet_id, Decimal("0.000"))
    taken = taken_kg_by_pallet([pallet_id]).get(pallet_id, Decimal("0.000"))
    left = as_kg(inbound - taken)
    return left if left > 0 else Decimal("0.000")


def physical_left(pallet_id: int) -> Decimal:
    """Вес, который ещё физически на паллете: приход минус отгрузка и переработка."""
    inbound = inbound_kg_by_pallet([pallet_id]).get(pallet_id, Decimal("0.000"))
    gone = _kg_by_pallet((Movement.Kind.OUT, Movement.Kind.TRANSFORM_OUT, Movement.Kind.WASTE), [pallet_id])
    left = as_kg(inbound - gone.get(pallet_id, 0))
    return left if left > 0 else Decimal("0.000")


def consumed_pallet_ids():
    """Паллеты без доступного веса и паллеты, ушедшие в переработку."""
    available = available_kg_by_pallet()
    gone = {pallet_id for pallet_id, qty in available.items() if qty <= 0}
    gone.update(reworked_pallet_ids())
    return list(gone)


def reworked_pallet_ids():
    return Movement.objects.filter(kind__in=(Movement.Kind.TRANSFORM_OUT, Movement.Kind.WASTE)).values_list(
        "pallet_id", flat=True
    )


def has_left(movement) -> bool:
    """По паллете уже есть отбор, отгрузка или переработка."""
    return Movement.objects.filter(pallet_id=movement.pallet_id, kind__in=LEFT_KINDS).exists()


def has_downstream(movement) -> bool:
    """Паллета целиком ушла из доступного остатка."""
    if unpicked_kg(movement.pallet_id) > 0:
        return False
    return Movement.objects.filter(pallet_id=movement.pallet_id, kind__in=LEFT_KINDS).exists()


def downstream_pallet_ids(pallet_ids) -> set[int]:
    ids = [item for item in pallet_ids if item]
    if not ids:
        return set()
    return set(Movement.objects.filter(pallet_id__in=ids, kind__in=LEFT_KINDS).values_list("pallet_id", flat=True))


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
    if has_left(movement):
        raise WarehouseError("По паллете уже есть отбор, отгрузка или переработка")
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
    if has_left(movement):
        raise WarehouseError("По паллете уже есть отбор, отгрузка или переработка")
    qty = as_kg(movement.qty_kg)
    if inbound_is_reserved(movement):
        _apply(_lock_balance(movement.lot_id), Decimal("0"), Decimal("0"), -qty)
    else:
        _apply(_lock_balance(movement.lot_id), -qty, Decimal("0"))
    movement.delete()


@transaction.atomic
def post_pick(*, task, task_line, pallet, source: Movement, user, qty_kg: Decimal) -> Movement:
    if source.kind != "in" or source.pallet_id != pallet.id:
        raise WarehouseError("Отбирать можно только принятую паллету")
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес отбора должен быть больше нуля")
    room = pallet_room(pallet.id, exclude_task_id=task.id)
    if qty > room:
        raise WarehouseError(f"На паллете недостаточно веса. Доступно {room} кг")
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
    # Отобранный вес уходит из остатка: из резерва, если паллета была под заказ, иначе из свободного.
    if inbound_is_reserved(source):
        _apply(_lock_balance(source.lot_id), Decimal("0"), Decimal("0"), -qty)
    else:
        _apply(_lock_balance(source.lot_id), -qty, Decimal("0"))
    if unpicked_kg(pallet.id) <= 0 and (pallet.reserved_line_id or pallet.reserved_consignee_id or pallet.order_id):
        pallet.reserved_line = None
        pallet.reserved_consignee = None
        pallet.order = None
        pallet.save(update_fields=["reserved_line", "reserved_consignee", "order"])
    return movement


@transaction.atomic
def post_out(*, task, task_line, pallet, source: Movement, user) -> Movement:
    if source.kind != "pick" or source.pallet_id != pallet.id:
        raise WarehouseError("Отгружается только отобранный вес")
    if Movement.objects.filter(source=source, kind="out").exists():
        raise WarehouseError("Этот отбор уже отгружен")
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
    return movement


def lock_balances(lot_ids) -> None:
    for lot_id in sorted(set(lot_ids)):
        _lock_balance(lot_id)


def _release_kg(lot_id: int, qty: Decimal, reserved: bool) -> None:
    qty = as_kg(qty)
    if qty <= 0:
        raise WarehouseError("Вес списания должен быть больше нуля")
    if reserved:
        _apply(_lock_balance(lot_id), Decimal("0"), Decimal("0"), -qty)
    else:
        _apply(_lock_balance(lot_id), -qty, Decimal("0"))


@transaction.atomic
def post_transform_out(*, pallet, lot, qty_kg: Decimal, user, document, reserved: bool, source: Movement) -> Movement:
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес списания должен быть больше нуля")
    movement = Movement.objects.create(
        pallet=pallet,
        kind=Movement.Kind.TRANSFORM_OUT,
        lot=lot,
        qty_kg=qty,
        source=source,
        created_by=user,
        transform_document=document,
    )
    _release_kg(lot.id, qty, reserved)
    return movement


@transaction.atomic
def post_waste(*, pallet, lot, qty_kg: Decimal, user, document, reserved: bool, source: Movement) -> Movement:
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес отхода должен быть больше нуля")
    movement = Movement.objects.create(
        pallet=pallet,
        kind=Movement.Kind.WASTE,
        lot=lot,
        qty_kg=qty,
        source=source,
        created_by=user,
        transform_document=document,
    )
    _release_kg(lot.id, qty, reserved)
    return movement


@transaction.atomic
def post_transform_in(*, pallet, lot, qty_kg: Decimal, user, document, reserved: bool = False) -> Movement:
    qty = as_kg(qty_kg)
    if qty <= 0:
        raise WarehouseError("Вес выхода должен быть больше нуля")
    movement = Movement.objects.create(
        pallet=pallet,
        kind=Movement.Kind.IN,
        lot=lot,
        qty_kg=qty,
        created_by=user,
        transform_document=document,
    )
    if reserved:
        _apply(_lock_balance(lot.id), Decimal("0"), Decimal("0"), qty)
    else:
        _apply(_lock_balance(lot.id), qty, Decimal("0"))
    return movement


def lot_reserve_rows(lot_id: int) -> list[dict]:
    """Строки заказов, под которые ещё стоит резерв этой партии."""
    from apps.common.present import describe_line

    movements = (
        Movement.objects.filter(kind="in", lot_id=lot_id)
        .select_related(
            "pallet__reserved_line__order__consignee",
            "pallet__reserved_line__product",
            "pallet__reserved_line__caliber",
            "pallet__reserved_line__caliber_from",
            "pallet__reserved_line__caliber_to",
            "pallet__order__consignee",
            "pallet__reserved_consignee",
        )
        .prefetch_related("pallet__reserved_line__grades")
        .order_by("id")
    )
    pallet_ids = [item.pallet_id for item in movements]
    inbound = inbound_kg_by_pallet(pallet_ids)
    taken = taken_kg_by_pallet(pallet_ids)
    groups: dict[tuple, dict] = {}
    seen: set[int] = set()
    for movement in movements:
        if movement.pallet_id in seen:
            continue
        seen.add(movement.pallet_id)
        pallet = movement.pallet
        if not (pallet.reserved_line_id or pallet.reserved_consignee_id or pallet.order_id):
            continue
        left = as_kg(inbound.get(pallet.id, 0) - taken.get(pallet.id, 0))
        if left <= 0:
            continue
        if pallet.reserved_line_id:
            key = ("line", pallet.reserved_line_id)
        elif pallet.order_id:
            key = ("order", pallet.order_id)
        else:
            key = ("consignee", pallet.reserved_consignee_id)
        bucket = groups.get(key)
        if bucket is None:
            bucket = _reserve_bucket(pallet, describe_line)
            groups[key] = bucket
        bucket["kg"] = as_kg(bucket["kg"] + left)
        bucket["pallets"].append({"id": pallet.id, "number": pallet.number or f"#{pallet.id}"})
    return sorted(groups.values(), key=lambda item: (item["order_number"], item["line_id"] or 0))


def _reserve_bucket(pallet, describe_line) -> dict:
    line = pallet.reserved_line
    order = line.order if line is not None and line.order_id else pallet.order
    consignee = ""
    if line is not None and line.order_id and line.order.consignee_id:
        consignee = line.order.consignee.name
    elif order is not None and order.consignee_id:
        consignee = order.consignee.name
    elif pallet.reserved_consignee_id:
        consignee = pallet.reserved_consignee.name
    return {
        "line_id": line.id if line is not None else None,
        "order_id": order.id if order is not None else None,
        "order_number": (order.number if order is not None else "") or "",
        "ship_date": order.ship_date if order is not None else None,
        "consignee": consignee,
        "title": describe_line(line) if line is not None else "Без строки заказа",
        "qty_kg": as_kg(line.qty_kg) if line is not None else None,
        "kg": Decimal("0.000"),
        "pallets": [],
    }


def lot_journal(lot_id: int) -> list[dict]:
    rows = (
        Movement.objects.filter(lot_id=lot_id)
        .select_related("pallet", "task", "transform_document", "created_by")
        .order_by("-id")
    )
    found = []
    for item in rows:
        document = ""
        href = ""
        if item.task_id:
            document = item.task.number or f"#{item.task_id}"
            href = f"/tasks/{item.task_id}"
        elif item.transform_document_id:
            document = item.transform_document.number or f"#{item.transform_document_id}"
            href = f"/transform/{item.transform_document_id}"
        found.append(
            {
                "created_at": item.created_at,
                "kind": item.get_kind_display(),
                "pallet_id": item.pallet_id,
                "pallet_number": item.pallet.number or f"#{item.pallet_id}",
                "qty_kg": as_kg(item.qty_kg),
                "document": document,
                "href": href,
                "user": item.created_by.name if item.created_by_id else "",
            }
        )
    return found


def stock_rows() -> list[dict]:
    rows = Balance.objects.select_related(
        "lot__product", "lot__caliber", "lot__grade", "lot__supplier", "lot__receipt"
    ).order_by("lot__received_on", "id")
    result = []
    for row in rows:
        if as_kg(row.free_kg) == 0 and as_kg(row.reserved_kg) == 0 and as_kg(row.picked_kg) == 0:
            continue
        lot = row.lot
        source = lot.get_source_display()
        if lot.is_opening:
            source = f"{source} · начальный"
        result.append(
            {
                "lot_id": lot.id,
                "number": lot.number or "",
                "product": lot.product.name,
                "caliber": lot.caliber.name,
                "grade": lot.grade.name,
                "supplier": lot.supplier.name,
                "source": source,
                "receipt": lot.receipt.number or "",
                "free_kg": as_kg(row.free_kg),
                "reserved_kg": as_kg(row.reserved_kg),
                "picked_kg": as_kg(row.picked_kg),
            }
        )
    return result
