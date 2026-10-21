import csv
import io
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch, Sum

from apps.catalog.families import critical_family
from apps.catalog.models import Caliber, Client, Consignee, Grade, Product, ProductAlias, Seller, Supplier
from apps.catalog.parties import ensure_alias, warehouse_product
from apps.common.constants import PALLET_TYPE, TASK_WORK_STATUS, task_status_choices
from apps.common.exceptions import WarehouseError
from apps.common.forms import get_row
from apps.common.kg import as_kg, format_kg
from apps.common.numbers import document_number
from apps.common.present import line_caliber
from apps.inventory.models import Balance, Movement
from apps.inventory.services import kg_by, received_kg
from apps.planning.models import Order, OrderLine, PurchaseOrder, PurchaseOrderLine, Task, TaskLine


def remaining_kg(line: OrderLine) -> Decimal:
    taken = Decimal("0.000")
    for task_line in line.task_lines.all():
        taken += as_kg(task_line.qty_kg)
    return as_kg(line.qty_kg) - taken


@transaction.atomic
def create_order(*, client_id, seller_id, ship_date, shipping_terms, user, consignee_id=None) -> Order:
    client = get_row(Client, client_id, "клиент")
    seller = get_row(Seller, seller_id, "продавец")
    from apps.catalog.parties import consignee_for_buyer

    consignee = get_row(Consignee, consignee_id, "грузополучатель") if consignee_id else consignee_for_buyer(client)
    order = Order.objects.create(
        client=client,
        consignee=consignee,
        seller=seller,
        ship_date=ship_date,
        shipping_terms=shipping_terms.strip(),
        created_by=user,
        status="open",
    )
    order.number = document_number("ЗК", order.id)
    order.save(update_fields=["number"])
    return order


def update_line_qty(line: OrderLine, qty_kg: Decimal) -> None:
    qty = as_kg(qty_kg)
    taken = as_kg(line.qty_kg) - remaining_kg(line)
    if qty < taken:
        raise WarehouseError("Количество меньше уже переданного в задания")
    line.qty_kg = qty
    line.save(update_fields=["qty_kg"])


def delete_line(line: OrderLine) -> None:
    if line.task_lines.exists():
        raise WarehouseError("По строке уже есть задания")
    line.delete()


def close_order(order: Order) -> None:
    order.status = "closed"
    order.save(update_fields=["status"])


def _line_has_movement(line: OrderLine) -> bool:
    return Movement.objects.filter(task_line__order_line_id=line.id).exists()


def posted_line_ids(order: Order) -> set[int]:
    ids = list(order.lines.values_list("id", flat=True))
    if not ids:
        return set()
    rows = TaskLine.objects.filter(order_line_id__in=ids, movements__isnull=False).values_list("order_line_id", flat=True)
    return set(rows)


def _fill_customer_line(line: OrderLine, item: dict) -> None:
    if item.get("keep") or _line_has_movement(line):
        update_line_qty(line, item["qty_kg"])
        return
    if item["product_id"] and not line.task_lines.exists():
        line.product = get_row(Product, item["product_id"], "номенклатура")
        line.save(update_fields=["product"])
    if not item.get("keep_caliber"):
        caliber = get_row(Caliber, item["caliber_id"], "калибр") if item["caliber_id"] else None
        if line.task_lines.exists() and caliber is None:
            raise WarehouseError(f"Укажите калибр для {line.product_title()}")
        line.caliber_mode = "exact" if caliber else "any"
        line.caliber = caliber
        line.caliber_from = None
        line.caliber_to = None
        line.save(update_fields=["caliber_mode", "caliber", "caliber_from", "caliber_to"])
    if not item.get("keep_grade"):
        if not item["grade_id"]:
            raise WarehouseError("Укажите сортность")
        line.grades.set([get_row(Grade, item["grade_id"], "сортность")])
    allowed = set(line.grades.values_list("id", flat=True))
    for task_line in line.task_lines.all():
        if line.caliber_mode == "exact" and line.caliber_id:
            task_line.caliber_id = line.caliber_id
            task_line.save(update_fields=["caliber"])
        grades = list(line.grades.all())
        if len(grades) == 1:
            task_line.grade = grades[0]
            task_line.save(update_fields=["grade"])
        elif task_line.grade_id not in allowed:
            raise WarehouseError(f"Сортность задания не входит в строку {line.product_title()}")
    update_line_qty(line, item["qty_kg"])


def _sync_customer_lines(order: Order, lines: list[dict]) -> None:
    kept = {item["line_id"] for item in lines if item["line_id"]}
    for line in list(order.lines.all()):
        if line.id not in kept:
            delete_line(line)
    by_id = {line.id: line for line in order.lines.all()}
    for item in lines:
        if item["line_id"]:
            line = by_id.get(item["line_id"])
            if line is None or line.order_id != order.id:
                raise WarehouseError("Строка не найдена")
            _fill_customer_line(line, item)
            continue
        if not item["product_id"]:
            continue
        line = OrderLine.objects.create(order=order, product_id=item["product_id"], qty_kg=as_kg(item["qty_kg"]))
        _fill_customer_line(line, item)


@transaction.atomic
def save_customer_order(order, *, client_id, seller_id, ship_date, shipping_terms, status, lines, user, consignee_id=None) -> Order:
    if status not in ("open", "closed"):
        raise WarehouseError("Выберите статус")
    was_closed = order is not None and order.status == "closed"
    if order is None:
        order = create_order(
            client_id=client_id,
            seller_id=seller_id,
            ship_date=ship_date,
            shipping_terms=shipping_terms,
            user=user,
            consignee_id=consignee_id,
        )
        _sync_customer_lines(order, lines)
        order.status = status
        order.save(update_fields=["status"])
        _check_party_grades(order)
        return order
    if was_closed:
        order.status = status
        order.save(update_fields=["status"])
        return order
    client = get_row(Client, client_id, "покупатель")
    seller = get_row(Seller, seller_id, "продавец")
    from apps.catalog.parties import consignee_for_buyer

    consignee = get_row(Consignee, consignee_id, "грузополучатель") if consignee_id else consignee_for_buyer(client)
    order.client = client
    order.consignee = consignee
    order.seller = seller
    order.ship_date = ship_date
    order.shipping_terms = shipping_terms.strip()
    order.status = status
    order.save()
    _sync_customer_lines(order, lines)
    _check_party_grades(order)
    return order


def _check_party_grades(order: Order) -> None:
    from apps.catalog.grade_policy import allowed_grade_ids

    party = allowed_grade_ids(order.consignee)
    if party is None:
        return
    for line in order.lines.prefetch_related("grades"):
        if not line.for_task:
            continue
        chosen = {item.id for item in line.grades.all()}
        if chosen and not chosen <= party:
            raise WarehouseError(
                f"Грузополучателю {order.consignee.name} нельзя сортность строки {line.product_title()}"
            )


def unmapped_titles(order: Order) -> list[str]:
    titles = []
    lines = order.lines.select_related("external_product__alias__product", "product")
    for line in lines:
        if not line.for_task or line.warehouse_product() is not None:
            continue
        titles.append(line.external_title() or "строка без товара")
    return titles


def _check_concrete(line: OrderLine, caliber: Caliber | None, grade: Grade | None) -> None:
    if line.caliber_mode == "exact":
        if caliber is None or caliber.id != line.caliber_id:
            raise WarehouseError("Калибр не совпадает со строкой заказа")
    elif line.caliber_mode == "range" and caliber is not None:
        start = line.caliber_from.sort_order if line.caliber_from_id else None
        end = line.caliber_to.sort_order if line.caliber_to_id else None
        if start is None or end is None or caliber.sort_order < start or caliber.sort_order > end:
            raise WarehouseError("Калибр вне диапазона строки заказа")
    if grade is None:
        return
    allowed = set(line.grades.values_list("id", flat=True))
    if allowed and grade.id not in allowed:
        raise WarehouseError("Сортность не разрешена строкой заказа")
    from apps.catalog.grade_policy import allowed_grade_ids

    consignee = line.order.consignee if line.order_id else None
    party = allowed_grade_ids(consignee)
    if party is not None and grade.id not in party:
        raise WarehouseError(f"Грузополучателю {consignee.name} нельзя сортность {grade.name}")


@transaction.atomic
def create_task(*, order_id, kind, supplier_id, ttn_number, doc_date, lines, user) -> Task:
    order = get_row(Order, order_id, "заказ")
    if order.status != "open":
        raise WarehouseError("Заказ закрыт")
    missing = unmapped_titles(order)
    if missing:
        raise WarehouseError(
            "Сначала сопоставьте номенклатуру 1С с номенклатурой: " + ", ".join(missing)
        )
    if kind not in ("receive", "pick", "ship"):
        raise WarehouseError("Выберите вид задания")
    if not lines:
        raise WarehouseError("Отметьте строки задания")
    supplier = None
    if kind == "receive":
        if not supplier_id:
            raise WarehouseError("Укажите поставщика")
        supplier = get_row(Supplier, supplier_id, "поставщик")
        if doc_date is None:
            raise WarehouseError("Укажите дату прихода")
    task = Task.objects.create(
        order=order,
        kind=kind,
        status=TASK_WORK_STATUS[kind],
        supplier=supplier,
        ttn_number=ttn_number.strip() if kind == "receive" else "",
        doc_date=doc_date if kind == "receive" else None,
        created_by=user,
    )
    task.number = document_number("ЗД", task.id)
    task.save(update_fields=["number"])
    for item in lines:
        order_line = get_row(OrderLine, item["order_line_id"], "строка заказа")
        if order_line.order_id != order.id:
            raise WarehouseError("Строка относится к другому заказу")
        if not order_line.for_task:
            continue
        product = order_line.warehouse_product()
        if product is None:
            title = order_line.external_title() or order_line.product_title()
            raise WarehouseError(f"Сначала сопоставьте номенклатуру 1С с номенклатурой: {title}")
        if order_line.product_id != product.id:
            order_line.product = product
            order_line.save(update_fields=["product"])
        caliber = get_row(Caliber, item["caliber_id"], "калибр") if item.get("caliber_id") else None
        grade = get_row(Grade, item["grade_id"], "сортность") if item.get("grade_id") else None
        _check_concrete(order_line, caliber, grade)
        qty = as_kg(item["qty_kg"])
        if qty <= 0:
            raise WarehouseError("Количество в задании должно быть больше нуля")
        TaskLine.objects.create(
            task=task,
            order_line=order_line,
            product_id=product.id,
            caliber=caliber,
            grade=grade,
            qty_kg=qty,
        )
    return task


def set_task_status(task: Task, status: str) -> None:
    allowed = {key for key, _label in task_status_choices(task.kind, task.status)}
    if status not in allowed:
        raise WarehouseError("Выберите статус")
    task.status = status
    task.save(update_fields=["status"])


def close_task(task: Task) -> None:
    set_task_status(task, "closed")


def close_task_order(task: Task):
    order = task.purchase_order or task.order
    if order is None:
        raise WarehouseError("У задания нет заказа")
    if order.status == "closed":
        return order
    still_open = [item.number or f"#{item.id}" for item in order.tasks.exclude(status="closed")]
    if still_open:
        raise WarehouseError("Сначала закройте задания: " + ", ".join(still_open))
    order.status = "closed"
    order.save(update_fields=["status"])
    return order


def delete_purchase_orders(ids: list[int]) -> tuple[list[str], list[str]]:
    from django.db.models.deletion import ProtectedError

    from apps.shipping.models import Shipment

    deleted = []
    failed = []
    orders = {order.id: order for order in PurchaseOrder.objects.filter(pk__in=ids)}
    for order_id in ids:
        order = orders.get(order_id)
        if order is None:
            failed.append(f"{order_id}: заказ не найден")
            continue
        number = order.number or f"#{order.id}"
        task_ids = list(order.tasks.values_list("id", flat=True))
        try:
            with transaction.atomic():
                if task_ids and (
                    Movement.objects.filter(task_id__in=task_ids).exists()
                    or Shipment.objects.filter(task_id__in=task_ids).exists()
                ):
                    raise WarehouseError("уже есть движения")
                if task_ids:
                    Task.objects.filter(pk__in=task_ids).delete()
                order.delete()
            deleted.append(number)
        except WarehouseError as exc:
            failed.append(f"{number}: {exc}")
        except ProtectedError:
            failed.append(f"{number}: заказ используется")
    return deleted, failed


def delete_customer_orders(ids: list[int]) -> tuple[list[str], list[str]]:
    from django.db.models.deletion import ProtectedError

    from apps.shipping.models import Allocation, Shipment
    from apps.storage.models import Pallet

    deleted = []
    failed = []
    orders = {order.id: order for order in Order.objects.filter(pk__in=ids)}
    for order_id in ids:
        order = orders.get(order_id)
        if order is None:
            failed.append(f"{order_id}: заказ не найден")
            continue
        number = order.number or f"#{order.id}"
        task_ids = list(order.tasks.values_list("id", flat=True))
        line_ids = list(order.lines.values_list("id", flat=True))
        try:
            with transaction.atomic():
                used = bool(Shipment.objects.filter(order_id=order.id).exists() or Pallet.objects.filter(order_id=order.id).exists())
                if task_ids and (
                    Movement.objects.filter(task_id__in=task_ids).exists()
                    or Shipment.objects.filter(task_id__in=task_ids).exists()
                ):
                    used = True
                if line_ids and Allocation.objects.filter(order_line_id__in=line_ids).exists():
                    used = True
                if used:
                    raise WarehouseError("уже есть движения")
                if task_ids:
                    Task.objects.filter(pk__in=task_ids).delete()
                order.delete()
            deleted.append(number)
        except WarehouseError as exc:
            failed.append(f"{number}: {exc}")
        except ProtectedError:
            failed.append(f"{number}: заказ используется")
    return deleted, failed


def delete_tasks(ids: list[int]) -> tuple[list[str], list[str]]:
    from django.db.models.deletion import ProtectedError

    from apps.shipping.models import Shipment

    deleted = []
    failed = []
    tasks = {task.id: task for task in Task.objects.filter(pk__in=ids)}
    for task_id in ids:
        task = tasks.get(task_id)
        if task is None:
            failed.append(f"{task_id}: задание не найдено")
            continue
        number = task.number or f"#{task.id}"
        try:
            with transaction.atomic():
                if Movement.objects.filter(task_id=task.id).exists() or Shipment.objects.filter(task_id=task.id).exists():
                    raise WarehouseError("уже есть движения")
                task.delete()
            deleted.append(number)
        except WarehouseError as exc:
            failed.append(f"{number}: {exc}")
        except ProtectedError:
            failed.append(f"{number}: задание используется")
    return deleted, failed


def _purchase_lines(order: PurchaseOrder, lines: list[dict]) -> None:
    order.lines.all().delete()
    for item in lines:
        product = get_row(Product, item["product_id"], "номенклатура") if item.get("product_id") else None
        external_code = (item.get("external_code") or "").strip()
        if product is None and not external_code:
            raise WarehouseError("Укажите номенклатуру")
        caliber = get_row(Caliber, item["caliber_id"], "калибр") if item.get("caliber_id") else None
        grade = get_row(Grade, item["grade_id"], "сортность") if item.get("grade_id") else None
        customer_line = None
        if item.get("customer_line_id"):
            customer_line = get_row(OrderLine, item["customer_line_id"], "строка заказа покупателя")
            if not customer_line.for_task:
                raise WarehouseError("Транспортные услуги нельзя ставить в резерв")
            if customer_line.order.status == "closed":
                raise WarehouseError("Заказ покупателя закрыт")
            theirs = customer_line.warehouse_product()
            if product is not None and theirs is not None and product.id != theirs.id:
                raise WarehouseError(f"Номенклатура не совпадает со строкой {customer_line.order.number}")
        qty = as_kg(item["qty_kg"])
        reserve = item.get("reserve_kg")
        if customer_line is not None and reserve is None:
            reserve = qty
        if customer_line is None:
            reserve = None
        PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            external_product_id=item.get("external_product_id"),
            external_code=external_code,
            external_name=(item.get("external_name") or "").strip(),
            unit=(item.get("unit") or "кг").strip() or "кг",
            for_task=item.get("for_task", True),
            customer_line=customer_line,
            reserve_kg=reserve,
            caliber=caliber,
            grade=grade,
            qty_kg=qty,
        )


def _open_receive_task(order: PurchaseOrder):
    return (
        Task.objects.filter(purchase_order=order, kind="receive", status=TASK_WORK_STATUS["receive"])
        .order_by("-id")
        .first()
    )


def _task_has_receipt(task: Task) -> bool:
    return Movement.objects.filter(task=task, kind="in").exists()


def _purchase_product(line: PurchaseOrderLine) -> Product | None:
    if line.product_id:
        return line.product
    external = line.external_product
    if external is None:
        return None
    product = None
    try:
        product = external.alias.product
    except ProductAlias.DoesNotExist:
        product = None
    if product is None:
        product = warehouse_product(critical_family(line.external_name or external.name or external.code))
        ensure_alias(external, product)
    line.product = product
    line.save(update_fields=["product"])
    return product


def _fill_receive_lines(task: Task, order: PurchaseOrder) -> None:
    lines = [
        line
        for line in order.lines.select_related("product", "external_product__alias__product")
        if line.for_task
    ]
    if not lines:
        raise WarehouseError("В заказе нет строк")
    for line in lines:
        if _purchase_product(line) is None:
            raise WarehouseError(f"В строке {line.product_title()} укажите номенклатуру")
    task.lines.all().delete()
    for line in lines:
        TaskLine.objects.create(
            task=task,
            purchase_line=line,
            product_id=line.product_id,
            caliber_id=line.caliber_id,
            grade_id=line.grade_id,
            qty_kg=as_kg(line.qty_kg),
        )


def _sync_receive_task(order: PurchaseOrder) -> None:
    task = _open_receive_task(order)
    if task is None or _task_has_receipt(task):
        return
    task.supplier_id = order.supplier_id
    task.doc_date = order.receipt_date or order.doc_date
    task.save(update_fields=["supplier", "doc_date"])
    _fill_receive_lines(task, order)


@transaction.atomic
def save_purchase_order(order, *, supplier_id, doc_date, receipt_date, status, comment, lines, user) -> PurchaseOrder:
    if status not in ("draft", "incoming", "closed"):
        raise WarehouseError("Выберите статус")
    was_closed = order is not None and order.status == "closed"
    if order is None:
        supplier = get_row(Supplier, supplier_id, "поставщик")
        order = PurchaseOrder.objects.create(
            supplier=supplier,
            doc_date=doc_date,
            receipt_date=receipt_date,
            status=status,
            comment=comment.strip(),
            created_by=user,
        )
        order.number = document_number("ЗП", order.id)
        order.save(update_fields=["number"])
        _purchase_lines(order, lines)
        return order
    if was_closed:
        order.status = status
        order.save(update_fields=["status"])
        return order
    supplier = get_row(Supplier, supplier_id, "поставщик")
    order.supplier = supplier
    order.doc_date = doc_date
    order.receipt_date = receipt_date
    order.status = status
    order.comment = comment.strip()
    order.save()
    _purchase_lines(order, lines)
    _sync_receive_task(order)
    return order


@transaction.atomic
def create_receive_task(order: PurchaseOrder, user) -> Task:
    if order.status == "closed":
        raise WarehouseError("Заказ закрыт")
    task = _open_receive_task(order)
    if task is not None and _task_has_receipt(task):
        raise WarehouseError(f"По заданию {task.number} уже есть приход")
    if task is None:
        task = Task.objects.create(
            purchase_order=order,
            kind="receive",
            status=TASK_WORK_STATUS["receive"],
            supplier_id=order.supplier_id,
            doc_date=order.receipt_date or order.doc_date,
            created_by=user,
        )
        task.number = document_number("ЗД", task.id)
        task.save(update_fields=["number"])
    else:
        task.supplier_id = order.supplier_id
        task.doc_date = order.receipt_date or order.doc_date
        task.save(update_fields=["supplier", "doc_date"])
    _fill_receive_lines(task, order)
    return task


def received_fact_csv(order: PurchaseOrder) -> bytes:
    movements = (
        Movement.objects.filter(task__purchase_order=order, kind="in")
        .select_related("task", "pallet__order__client", "pallet__reserved_line", "lot__product", "lot__caliber", "lot__grade")
        .prefetch_related("pallet__units__packaging")
        .order_by("id")
    )
    if not movements:
        raise WarehouseError("По заказу ещё нет принятого товара")
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Заказ",
            "Поставщик",
            "Задание",
            "Паллета",
            "Вид паллета",
            "Номенклатура",
            "Калибр",
            "Сортность",
            "Упаковка",
            "Упаковок",
            "Грязный вес, кг",
            "Чистый вес, кг",
            "Резерв",
        ]
    )
    total = Decimal("0.000")
    for movement in movements:
        units = list(movement.pallet.units.all())
        gross = sum((as_kg(unit.gross_kg) for unit in units), Decimal("0.000"))
        net = as_kg(movement.qty_kg)
        total += net
        packaging = units[0].packaging.name if units else ""
        writer.writerow(
            [
                order.number,
                order.supplier.name,
                movement.task.number,
                movement.pallet.number,
                PALLET_TYPE.get(movement.pallet.pallet_type, ""),
                movement.lot.product.name,
                movement.lot.caliber.name,
                movement.lot.grade.name,
                packaging,
                len(units),
                format_kg(gross),
                format_kg(net),
                movement.pallet.order.number if movement.pallet.order_id else "",
            ]
        )
    writer.writerow(["", "", "", "", "", "", "", "", "", "Итого", "", format_kg(total), ""])
    return buffer.getvalue().encode("utf-8")


def _demand_spec(line: OrderLine, consignee) -> str:
    from apps.catalog.grade_policy import effective_grade_names

    parts = [line.product_title()]
    caliber = line_caliber(line)
    if caliber:
        parts.append(caliber)
    grades = effective_grade_names(line, consignee)
    if grades:
        parts.append(f"сортность: {grades}")
    return ", ".join(parts)


def _demand_grade(line: OrderLine, consignee) -> str:
    from apps.catalog.grade_policy import effective_grade_names

    return effective_grade_names(line, consignee)


def _covers(line: OrderLine, product_id: int, caliber, grade_id) -> bool:
    if product_id != line.product_id:
        return False
    if line.caliber_mode == "exact":
        if caliber is None or caliber.id != line.caliber_id:
            return False
    elif line.caliber_mode == "range":
        start = line.caliber_from.sort_order if line.caliber_from_id else None
        end = line.caliber_to.sort_order if line.caliber_to_id else None
        if caliber is None or start is None or end is None or caliber.sort_order < start or caliber.sort_order > end:
            return False
    from apps.catalog.grade_policy import effective_grade_ids

    consignee = line.order.consignee if line.order_id else None
    grades = effective_grade_ids(line, consignee)
    if grades is not None and grade_id not in grades:
        return False
    return True


def _take(pools: list[dict], line: OrderLine, need: Decimal) -> Decimal:
    left = as_kg(need)
    taken = Decimal("0.000")
    if left <= 0:
        return taken
    for pool in pools:
        if pool["kg"] <= 0 or not _covers(line, pool["product_id"], pool["caliber"], pool["grade_id"]):
            continue
        chunk = min(pool["kg"], left)
        pool["kg"] = as_kg(pool["kg"] - chunk)
        taken += chunk
        left = as_kg(left - chunk)
        if left <= 0:
            break
    return as_kg(taken)


def demand_report() -> dict:
    from apps.catalog.grade_policy import effective_grade_ids
    from apps.planning.reserve import (
        assigned_by_order_line,
        client_reserved_pools,
        holding_by_grade,
        legacy_reserved_pools,
        plan_kg,
    )

    picked = kg_by("order_line_id", "pick")
    received = kg_by("purchase_line_id", "in")
    held = holding_by_grade()
    assigned = assigned_by_order_line()
    legacy = legacy_reserved_pools()
    client_holds = client_reserved_pools()
    stock_pools = []
    for row in Balance.objects.select_related("lot__caliber").order_by("lot__received_on", "lot_id"):
        kg = as_kg(row.free_kg)
        if kg <= 0:
            continue
        lot = row.lot
        stock_pools.append(
            {"product_id": lot.product_id, "caliber": lot.caliber, "grade_id": lot.grade_id, "kg": kg}
        )
    purchases = PurchaseOrder.objects.filter(status="incoming").select_related("supplier").prefetch_related(
        "lines__product__group",
        "lines__caliber",
        "lines__grade",
        "lines__customer_line__grades",
        "lines__customer_line__order__consignee__allowed_grades",
    )
    purchases = sorted(purchases, key=lambda item: (item.receipt_date or date.max, item.id))
    inbound = []
    incoming_pools = []
    dedicated: dict[int, Decimal] = {}
    for order in purchases:
        for line in order.lines.all():
            if not line.for_task or not line.product_id:
                continue
            ordered = as_kg(line.qty_kg)
            got = received.get(line.id, Decimal("0.000"))
            left = as_kg(ordered - got)
            if left < 0:
                left = Decimal("0.000")
            shared_left = left
            if line.customer_line_id:
                customer = line.customer_line
                consignee = customer.order.consignee if customer.order_id else None
                allowed = effective_grade_ids(customer, consignee)
                fits = allowed is None or (line.grade_id is not None and line.grade_id in allowed)
                if fits:
                    plan = plan_kg(line)
                    covered = assigned.get(line.customer_line_id, Decimal("0.000"))
                    used = min(covered, plan)
                    assigned[line.customer_line_id] = as_kg(covered - used)
                    still = as_kg(plan - used)
                    named = min(left, still)
                    dedicated[line.customer_line_id] = as_kg(dedicated.get(line.customer_line_id, Decimal("0.000")) + named)
                    shared_left = as_kg(left - named)
            if left <= 0:
                continue
            inbound.append(
                {
                    "order_id": order.id,
                    "order_number": order.number,
                    "supplier": order.supplier.name,
                    "receipt_date": order.receipt_date,
                    "product": line.product.name,
                    "category_id": line.product.group_id or 0,
                    "category": line.product.group.name if line.product.group_id else "Без группы",
                    "caliber": line.caliber.name if line.caliber_id else "",
                    "grade": line.grade.name if line.grade_id else "",
                    "ordered_kg": ordered,
                    "received_kg": got,
                    "left_kg": left,
                }
            )
            if shared_left > 0 and line.caliber_id and line.grade_id:
                incoming_pools.append(
                    {
                        "product_id": line.product_id,
                        "caliber": line.caliber,
                        "grade_id": line.grade_id,
                        "kg": shared_left,
                    }
                )
    orders = (
        Order.objects.filter(status="open")
        .select_related("client", "consignee")
        .prefetch_related(
            "consignee__allowed_grades",
            Prefetch(
                "lines",
                queryset=OrderLine.objects.select_related(
                    "product__group",
                    "caliber",
                    "caliber_from",
                    "caliber_to",
                    "external_product__group",
                    "external_product__alias__product__group",
                ).prefetch_related("grades"),
            )
        )
        .order_by("ship_date", "id")
    )
    lines = []
    short_total = Decimal("0.000")
    order_list = list(orders)
    for order in order_list:
        for line in order.lines.all():
            if not line.for_task:
                continue
            ordered = as_kg(line.qty_kg)
            already = picked.get(line.id, Decimal("0.000"))
            open_need = as_kg(ordered - already)
            if open_need < 0:
                open_need = Decimal("0.000")
            allowed = effective_grade_ids(line, order.consignee)
            usable = Decimal("0.000")
            for grade_id, kg in held.get(line.id, []):
                if allowed is None or grade_id in allowed:
                    usable += kg
            from_hold = min(as_kg(usable), open_need)
            left = as_kg(open_need - from_hold)
            from_legacy = _take(legacy.get(order.id, []), line, left)
            left = as_kg(left - from_legacy)
            from_client = _take(client_holds.get(order.client_id, []), line, left)
            left = as_kg(left - from_client)
            from_stock = _take(stock_pools, line, left)
            left = as_kg(left - from_stock)
            from_named = min(dedicated.get(line.id, Decimal("0.000")), left)
            dedicated[line.id] = as_kg(dedicated.get(line.id, Decimal("0.000")) - from_named)
            left = as_kg(left - from_named)
            from_incoming = _take(incoming_pools, line, left)
            from_stock = as_kg(from_hold + from_legacy + from_client + from_stock)
            from_incoming = as_kg(from_named + from_incoming)
            short = as_kg(open_need - from_stock - from_incoming)
            if short > 0:
                short_total += short
            category_id, category = _line_category(line)
            lines.append(
                {
                    "order_id": order.id,
                    "order_number": order.number,
                    "ship_date": order.ship_date,
                    "client": order.client.name,
                    "consignee": order.consignee.name if order.consignee_id else order.client.name,
                    "product": line.product_title(),
                    "category_id": category_id,
                    "category": category,
                    "spec": _demand_spec(line, order.consignee),
                    "grade": _demand_grade(line, order.consignee),
                    "ordered_kg": ordered,
                    "picked_kg": already,
                    "stock_kg": from_stock,
                    "incoming_kg": from_incoming,
                    "short_kg": short,
                }
            )
    return {
        "has_orders": bool(order_list),
        "lines": lines,
        "short_total": as_kg(short_total),
        "inbound": inbound,
        "inbound_total": as_kg(sum((row["left_kg"] for row in inbound), Decimal("0.000"))),
    }


def _line_category(line) -> tuple[int, str]:
    product = line.warehouse_product()
    if product is None and line.external_product_id:
        product = line.external_product
    group = product.group if product is not None and product.group_id else None
    if group is None:
        return 0, "Без группы"
    return group.id, group.name


def _demand_sums(rows: list[dict]) -> dict:
    zero = Decimal("0.000")
    return {
        "count": len(rows),
        "ordered_kg": as_kg(sum((row["ordered_kg"] for row in rows), zero)),
        "picked_kg": as_kg(sum((row["picked_kg"] for row in rows), zero)),
        "stock_kg": as_kg(sum((row["stock_kg"] for row in rows), zero)),
        "incoming_kg": as_kg(sum((row["incoming_kg"] for row in rows), zero)),
        "short_kg": as_kg(sum((row["short_kg"] for row in rows), zero)),
    }


def _demand_nodes(lines: list[dict], modes: list[str], level: int, ordering=None) -> list[dict]:
    fields = {"date": "ship_date", "consignee": "consignee", "category": "category", "product": "product", "grade": "grade"}
    if not modes:
        if ordering:
            rows = sorted(lines, key=ordering)
        else:
            rows = sorted(
                lines,
                key=lambda row: (row["ship_date"], row["consignee"], row["category"], row["product"], row["order_number"], row["spec"]),
            )
        return [{"title": "", "rows": rows, "children": [], "level": level, "pad": 8, **_demand_sums(rows)}]
    field = fields[modes[0]]
    buckets: dict = {}
    keys = []
    for row in lines:
        key = row[field]
        if key not in buckets:
            buckets[key] = []
            keys.append(key)
        buckets[key].append(row)
    if modes[0] == "date":
        keys.sort()
    else:
        keys.sort(key=lambda item: str(item).casefold())
    nodes = []
    for key in keys:
        chunk = buckets[key]
        title = key.strftime("%d.%m.%Y") if modes[0] == "date" else str(key)
        if modes[0] == "grade" and not title:
            title = "не задана"
        kids = _demand_nodes(chunk, modes[1:], level + 1, ordering)
        nodes.append(
            {
                "title": title,
                "rows": [],
                "children": kids,
                "level": level,
                "pad": 8 + level * 18,
                **_demand_sums(chunk),
            }
        )
    return nodes


def group_demand(lines: list[dict], modes, ordering=None) -> list[dict]:
    if isinstance(modes, str):
        modes = [modes] if modes else []
    if not lines:
        return []
    return _demand_nodes(lines, list(modes), 0, ordering)


def sum_kg(lines) -> Decimal:
    return sum((as_kg(line.qty_kg) for line in lines if line.for_task), Decimal("0.000"))
