from datetime import date

from apps.common.constants import PURCHASE_STATUS
from apps.common.kg import as_kg
from apps.planning.models import PurchaseOrder, PurchaseOrderRevision
from apps.planning.revisions import empty_changes, has_diff

HEADER_FIELDS = (
    ("supplier", "Поставщик"),
    ("doc_date", "Дата"),
    ("receipt_date", "Дата поступления"),
    ("status", "Состояние"),
    ("comment", "Комментарий"),
)


def header_snapshot(order: PurchaseOrder) -> dict:
    return {
        "supplier": order.supplier.name,
        "doc_date": order.doc_date.isoformat(),
        "receipt_date": order.receipt_date.isoformat() if order.receipt_date else "",
        "status": order.status,
        "comment": (order.comment or "").strip(),
    }


def line_snapshot(order: PurchaseOrder) -> list[dict]:
    rows = []
    for line in order.lines.select_related("product", "caliber", "grade").order_by("id"):
        if line.external_code:
            rows.append(
                {
                    "code": line.external_code,
                    "name": line.external_name or line.product_title(),
                    "qty": str(as_kg(line.qty_kg)),
                }
            )
            continue
        name = line.product_title()
        if line.caliber_id:
            name = f"{name}, {line.caliber.name}"
        if line.grade_id:
            name = f"{name}, {line.grade.name}"
        rows.append(
            {
                "code": f"p{line.product_id}:{line.caliber_id or 0}:{line.grade_id or 0}",
                "name": name,
                "qty": str(as_kg(line.qty_kg)),
            }
        )
    return rows


def incoming_lines(lines: list[dict]) -> list[dict]:
    return [
        {
            "code": item["external_code"],
            "name": item["external_name"],
            "qty": str(as_kg(item["qty_kg"])),
        }
        for item in lines
    ]


def _show_date(value: str) -> str:
    if not value:
        return ""
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def _show(field: str, value: str) -> str:
    if field in ("doc_date", "receipt_date"):
        return _show_date(value)
    if field == "status":
        return PURCHASE_STATUS.get(value, value)
    return value


def diff_purchase(before_header: dict, before_lines: list[dict], after_header: dict, after_lines: list[dict]) -> dict:
    changes = empty_changes()
    for key, label in HEADER_FIELDS:
        old = before_header.get(key) or ""
        new = after_header.get(key) or ""
        if old != new:
            changes["header"].append({"field": label, "before": _show(key, old), "after": _show(key, new)})
    before = {}
    after = {}
    for row in before_lines:
        before.setdefault(row["code"], []).append(row)
    for row in after_lines:
        after.setdefault(row["code"], []).append(row)
    for code in sorted(set(before) | set(after)):
        old_rows = sorted(before.get(code, []), key=lambda row: (row["name"], row["qty"]))
        new_rows = sorted(after.get(code, []), key=lambda row: (row["name"], row["qty"]))
        paired = min(len(old_rows), len(new_rows))
        for index in range(paired):
            old = old_rows[index]
            new = new_rows[index]
            if old["qty"] == new["qty"] and old["name"] == new["name"]:
                continue
            changes["changed"].append(
                {
                    "code": code,
                    "name": new["name"],
                    "before_name": old["name"],
                    "before": old["qty"],
                    "after": new["qty"],
                }
            )
        changes["added"].extend(new_rows[paired:])
        changes["removed"].extend(old_rows[paired:])
    return changes


def _date(value: str):
    return date.fromisoformat(value) if value else None


def _store(order: PurchaseOrder, revision: int, header: dict, lines: list[dict], changes: dict, user) -> None:
    PurchaseOrderRevision.objects.create(
        order=order,
        revision=revision,
        doc_date=date.fromisoformat(header["doc_date"]),
        receipt_date=_date(header["receipt_date"]),
        supplier_name=header["supplier"],
        status=header["status"],
        comment=header["comment"],
        lines=lines,
        changes=changes,
        created_by=user,
    )


def remember_version(order: PurchaseOrder, before_header: dict, before_lines: list[dict], user) -> int | None:
    if not order.revisions.exists():
        baseline = PurchaseOrderRevision.objects.create(
            order=order,
            revision=1,
            doc_date=date.fromisoformat(before_header["doc_date"]),
            receipt_date=_date(before_header["receipt_date"]),
            supplier_name=before_header["supplier"],
            status=before_header["status"],
            comment=before_header["comment"],
            lines=before_lines,
            changes=empty_changes(),
            created_by=user,
        )
        baseline.created_at = order.created_at
        baseline.save(update_fields=["created_at"])
    after_header = header_snapshot(order)
    after_lines = line_snapshot(order)
    actual = diff_purchase(before_header, before_lines, after_header, after_lines)
    if not has_diff(actual):
        return None
    next_revision = order.revisions.order_by("-revision").values_list("revision", flat=True).first() + 1
    _store(order, next_revision, after_header, after_lines, actual, user)
    order.revision = next_revision
    order.save(update_fields=["revision"])
    return next_revision


def store_baseline(order: PurchaseOrder, user) -> None:
    if order.revisions.exists():
        return
    _store(order, order.revision or 1, header_snapshot(order), line_snapshot(order), empty_changes(), user)


def apply_purchase_import(
    order: PurchaseOrder,
    user,
    *,
    supplier,
    doc_date,
    receipt_date,
    status: str,
    comment: str,
    lines: list[dict],
) -> dict:
    before_header = header_snapshot(order)
    before_lines = line_snapshot(order)
    after_header = {
        "supplier": supplier.name,
        "doc_date": doc_date.isoformat(),
        "receipt_date": receipt_date.isoformat() if receipt_date else "",
        "status": status,
        "comment": (comment or "").strip(),
    }
    wanted = diff_purchase(before_header, before_lines, after_header, incoming_lines(lines))
    if not has_diff(wanted):
        return {"kind": "same", "changes": wanted}
    from apps.inventory.models import Movement

    received = Movement.objects.filter(task__purchase_order=order, kind="in").exists()
    if order.status == "closed" or received:
        return {"kind": "closed", "changes": wanted}
    order.supplier = supplier
    order.doc_date = doc_date
    order.receipt_date = receipt_date
    order.status = status
    order.comment = (comment or "").strip()
    order.save()
    blocked = _apply_lines(order, lines)
    wanted["blocked"] = blocked
    revision = remember_version(order, before_header, before_lines, user)
    if revision is None:
        return {"kind": "blocked", "changes": wanted, "revision": order.revision}
    return {"kind": "updated", "changes": wanted, "revision": revision}


def _apply_lines(order: PurchaseOrder, lines: list[dict]) -> list[dict]:
    from apps.planning.models import PurchaseOrderLine

    blocked = []
    pools: dict[str, list[PurchaseOrderLine]] = {}
    for line in order.lines.all().order_by("id"):
        if not line.external_code:
            continue
        pools.setdefault(line.external_code, []).append(line)
    used = set()
    for item in lines:
        pool = pools.get(item["external_code"]) or []
        line = next((row for row in pool if row.id not in used), None)
        if line is None:
            PurchaseOrderLine.objects.create(
                order=order,
                product_id=item["product_id"],
                external_product_id=item["external_product_id"],
                caliber_id=item.get("caliber_id"),
                grade_id=item.get("grade_id"),
                qty_kg=as_kg(item["qty_kg"]),
                external_code=item["external_code"],
                external_name=item["external_name"],
                unit=item.get("unit") or "кг",
                for_task=item.get("for_task", True),
            )
            continue
        used.add(line.id)
        if line.task_lines.exists() and as_kg(line.qty_kg) != as_kg(item["qty_kg"]):
            blocked.append({"code": item["external_code"], "name": item["external_name"], "text": "строка уже в задании"})
            continue
        line.external_name = item["external_name"]
        line.external_product_id = item["external_product_id"]
        line.unit = item.get("unit") or "кг"
        line.for_task = item.get("for_task", True)
        line.qty_kg = as_kg(item["qty_kg"])
        if item["product_id"] and not line.task_lines.exists():
            line.product_id = item["product_id"]
        line.save()
    for pool in pools.values():
        for line in pool:
            if line.id in used:
                continue
            if line.task_lines.exists():
                blocked.append(
                    {
                        "code": line.external_code,
                        "name": line.external_name or line.product_title(),
                        "text": "строка уже в задании",
                    }
                )
                continue
            line.delete()
    return blocked
