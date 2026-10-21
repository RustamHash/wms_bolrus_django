from datetime import date

from apps.common.exceptions import WarehouseError
from apps.common.kg import as_kg
from apps.planning.models import Order, OrderLine, OrderRevision
from apps.planning.services import delete_line, update_line_qty

HEADER_FIELDS = (
    ("client", "Покупатель"),
    ("consignee", "Грузополучатель"),
    ("seller", "Продавец"),
    ("ship_date", "Дата отгрузки"),
    ("terms", "Условия отгрузки"),
)


def empty_changes() -> dict:
    return {"header": [], "added": [], "removed": [], "changed": [], "blocked": []}


def has_diff(changes: dict) -> bool:
    return any(changes.get(key) for key in ("header", "added", "removed", "changed", "blocked"))


def revision_summary(changes: dict) -> str:
    if not changes:
        return "Исходная версия"
    parts = []
    header = changes.get("header") or []
    if header:
        parts.append(", ".join(item["field"] for item in header))
    added = changes.get("added") or []
    if added:
        parts.append(f"добавлено строк: {len(added)}")
    removed = changes.get("removed") or []
    if removed:
        parts.append(f"снято строк: {len(removed)}")
    changed = changes.get("changed") or []
    if changed:
        parts.append(f"изменено строк: {len(changed)}")
    blocked = changes.get("blocked") or []
    if blocked:
        parts.append(f"не применено: {len(blocked)}")
    return "; ".join(parts) or "Исходная версия"


def header_snapshot(order: Order) -> dict:
    consignee = order.consignee.name if order.consignee_id else order.client.name
    return {
        "client": order.client.name,
        "consignee": consignee,
        "seller": order.seller.name,
        "ship_date": order.ship_date.isoformat(),
        "terms": (order.shipping_terms or "").strip(),
    }


def line_snapshot(order: Order) -> list[dict]:
    rows = []
    for line in order.lines.all().order_by("id"):
        if not line.external_code:
            continue
        rows.append(
            {
                "code": line.external_code,
                "name": line.external_name or line.product_title(),
                "qty": str(as_kg(line.qty_kg)),
            }
        )
    return rows


def incoming_header(*, client_name, consignee_name, seller_name, ship_date: date, terms: str) -> dict:
    return {
        "client": client_name,
        "consignee": consignee_name or client_name,
        "seller": seller_name,
        "ship_date": ship_date.isoformat(),
        "terms": (terms or "").strip(),
    }


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
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def _show_header(field: str, value: str) -> str:
    if field == "ship_date":
        return _show_date(value)
    return value


def diff_states(before_header: dict, before_lines: list[dict], after_header: dict, after_lines: list[dict]) -> dict:
    changes = empty_changes()
    for key, label in HEADER_FIELDS:
        old = before_header.get(key) or ""
        new = after_header.get(key) or ""
        if old != new:
            changes["header"].append(
                {"field": label, "before": _show_header(key, old), "after": _show_header(key, new)}
            )
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


def _store(order: Order, revision: int, header: dict, lines: list[dict], changes: dict, user) -> None:
    OrderRevision.objects.create(
        order=order,
        revision=revision,
        ship_date=date.fromisoformat(header["ship_date"]),
        client_name=header["client"],
        consignee_name=header["consignee"],
        seller_name=header["seller"],
        shipping_terms=header["terms"],
        lines=lines,
        changes=changes,
        created_by=user,
    )


def remember_version(order: Order, before_header: dict, before_lines: list[dict], changes: dict, user) -> int | None:
    if not order.revisions.exists():
        baseline = OrderRevision.objects.create(
            order=order,
            revision=1,
            ship_date=date.fromisoformat(before_header["ship_date"]),
            client_name=before_header["client"],
            consignee_name=before_header["consignee"],
            seller_name=before_header["seller"],
            shipping_terms=before_header["terms"],
            lines=before_lines,
            changes=empty_changes(),
            created_by=user,
        )
        baseline.created_at = order.created_at
        baseline.save(update_fields=["created_at"])
    after_header = header_snapshot(order)
    after_lines = line_snapshot(order)
    actual = diff_states(before_header, before_lines, after_header, after_lines)
    actual["blocked"] = changes.get("blocked") or []
    if not has_diff(actual):
        return None
    next_revision = order.revisions.order_by("-revision").values_list("revision", flat=True).first() + 1
    _store(order, next_revision, after_header, after_lines, actual, user)
    order.revision = next_revision
    order.save(update_fields=["revision"])
    return next_revision


def store_baseline(order: Order, user) -> None:
    if order.revisions.exists():
        return
    _store(order, order.revision or 1, header_snapshot(order), line_snapshot(order), empty_changes(), user)


def apply_import(order: Order, user, *, client, consignee, seller, ship_date, terms: str, lines: list[dict]) -> dict:
    before_header = header_snapshot(order)
    before_lines = line_snapshot(order)
    wanted = diff_states(
        before_header,
        before_lines,
        incoming_header(
            client_name=client.name,
            consignee_name=consignee.name,
            seller_name=seller.name,
            ship_date=ship_date,
            terms=terms,
        ),
        incoming_lines(lines),
    )
    if not has_diff(wanted):
        return {"kind": "same", "changes": wanted}
    if order.status != "open":
        return {"kind": "closed", "changes": wanted}
    order.client = client
    order.consignee = consignee
    order.seller = seller
    order.ship_date = ship_date
    order.shipping_terms = (terms or "").strip()
    order.save()
    blocked = _apply_lines(order, lines)
    from apps.catalog.grade_policy import stamp_order_grades

    stamp_order_grades(order)
    wanted["blocked"] = blocked
    revision = remember_version(order, before_header, before_lines, wanted, user)
    if revision is None:
        return {"kind": "blocked", "changes": wanted, "revision": order.revision}
    return {"kind": "updated", "changes": wanted, "revision": revision}


def _apply_lines(order: Order, lines: list[dict]) -> list[dict]:
    blocked = []
    pools: dict[str, list[OrderLine]] = {}
    for line in order.lines.all().order_by("id"):
        if not line.external_code:
            continue
        pools.setdefault(line.external_code, []).append(line)
    used = set()
    for item in lines:
        pool = pools.get(item["external_code"]) or []
        line = next((row for row in pool if row.id not in used), None)
        if line is None:
            OrderLine.objects.create(
                order=order,
                product_id=item["product_id"],
                external_product_id=item["external_product_id"],
                caliber_mode="any",
                qty_kg=as_kg(item["qty_kg"]),
                external_code=item["external_code"],
                external_name=item["external_name"],
                unit=item.get("unit") or "кг",
                for_task=item.get("for_task", True),
            )
            continue
        used.add(line.id)
        fields = []
        if line.external_name != item["external_name"]:
            line.external_name = item["external_name"]
            fields.append("external_name")
        if line.external_product_id != item["external_product_id"]:
            line.external_product_id = item["external_product_id"]
            fields.append("external_product")
        if item["product_id"] and line.product_id != item["product_id"] and not line.task_lines.exists():
            line.product_id = item["product_id"]
            fields.append("product")
        unit = item.get("unit") or "кг"
        if line.unit != unit:
            line.unit = unit
            fields.append("unit")
        for_task = item.get("for_task", True)
        if line.for_task != for_task:
            line.for_task = for_task
            fields.append("for_task")
        if fields:
            line.save(update_fields=fields)
        if as_kg(line.qty_kg) == as_kg(item["qty_kg"]):
            continue
        try:
            update_line_qty(line, item["qty_kg"])
        except WarehouseError as exc:
            blocked.append({"code": item["external_code"], "name": item["external_name"], "text": str(exc)})
    for pool in pools.values():
        for line in pool:
            if line.id in used:
                continue
            try:
                delete_line(line)
            except WarehouseError as exc:
                blocked.append(
                    {
                        "code": line.external_code,
                        "name": line.external_name or line.product_title(),
                        "text": str(exc),
                    }
                )
    return blocked
