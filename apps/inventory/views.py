from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.catalog.models import ProductGroup
from apps.common.forms import query_date
from apps.common.http import roles_required
from apps.common.kg import as_kg
from apps.common.xlsx import write_book
from apps.inventory.models import Balance
from apps.inventory.services import lot_journal, lot_reserve_rows, stock_rows
from apps.receiving.models import Lot
from apps.planning.services import demand_report, group_demand

GROUP_FIELDS = (
    ("date", "Дата отгрузки"),
    ("consignee", "Грузополучатель"),
    ("category", "Группа товара"),
    ("product", "Товар"),
    ("grade", "Сортность"),
)
DEMAND_COLUMNS = (
    ("order", "Заказ", False),
    ("ship_date", "Отгрузка", False),
    ("consignee", "Грузополучатель", False),
    ("category", "Группа товара", False),
    ("product", "Товар", False),
    ("grade", "Сортность", False),
    ("spec", "Спецификация", False),
    ("ordered", "Заказано", True),
    ("picked", "Отобрано", True),
    ("reserve", "В резерве", True),
    ("free", "Свободно своей категории", True),
    ("blocked", "Есть, но нельзя", True),
    ("incoming", "Ожидается приход", True),
    ("short", "Не хватает", True),
)
DEFAULT_COLUMNS = ("order", "ship_date", "consignee", "spec", "ordered", "picked", "reserve", "free", "blocked", "incoming", "short")
PICK_FIELDS = (
    ("order_number", "Заказ"),
    ("ship_date", "Отгрузка"),
    ("consignee", "Грузополучатель"),
    ("category", "Группа товара"),
    ("product", "Товар"),
    ("grade", "Сортность"),
    ("spec", "Спецификация"),
    ("ordered", "Заказано"),
    ("picked", "Отобрано"),
    ("reserve", "В резерве"),
    ("free", "Свободно своей категории"),
    ("blocked", "Есть, но нельзя"),
    ("incoming", "Ожидается приход"),
    ("short", "Не хватает"),
)
PICK_OPS = (
    ("equals", "Равно"),
    ("not_equals", "Не равно"),
    ("contains", "Содержит"),
    ("not_contains", "Не содержит"),
    ("starts_with", "Начинается с"),
    ("ends_with", "Заканчивается на"),
    ("in_list", "В списке"),
    ("not_in_list", "Не в списке"),
    ("is_empty", "Пусто"),
    ("is_not_empty", "Не пусто"),
    ("greater", "Больше"),
    ("less", "Меньше"),
    ("greater_or_equal", "Больше или равно"),
    ("less_or_equal", "Меньше или равно"),
)
OP_ALIAS = {"eq": "equals", "ne": "not_equals"}
INBOUND_PICKS = {"order_number", "category", "product", "ordered"}
VALUE_KEYS = {
    "order": "order_number",
    "order_number": "order_number",
    "ordered": "ordered_kg",
    "picked": "picked_kg",
    "reserve": "reserve_kg",
    "free": "free_kg",
    "blocked": "blocked_kg",
    "stock": "stock_kg",
    "incoming": "incoming_kg",
    "short": "short_kg",
}


@roles_required("receiver", "warehouse")
def reports_home(request):
    return render(
        request,
        "hub.html",
        {
            "title": "Отчеты",
            "items": [
                {"href": "/stock", "title": "Остатки"},
                {"href": "/demand", "title": "Потребность"},
                {"href": "/reports/nomenclature", "title": "Номенклатура 1С"},
                {"href": "/orders/customers/revisions", "title": "Расхождения заказов"},
                {"href": "/pallets", "title": "Паллеты"},
                {"href": "/transform", "title": "Переработка"},
            ],
        },
    )


@roles_required("receiver", "warehouse")
def nomenclature_report(request):
    from apps.onec.exceptions import OnecError
    from apps.onec.nomenclature import nomenclature_rows

    try:
        rows = nomenclature_rows()
    except OnecError as exc:
        messages.error(request, str(exc))
        return redirect("/reports")
    header = [
        ("Код", "header"),
        ("Наименование", "header"),
        ("Артикул", "header"),
        ("Группа", "header"),
        ("Путь", "header"),
        ("Единица", "header"),
        ("Это группа", "header"),
        ("Пометка удаления", "header"),
    ]
    sheet = [header]
    for row in rows:
        sheet.append(
            [
                row["code"],
                row["name"],
                row["article"],
                row["group"],
                row["path"],
                row["unit"],
                "да" if row["folder"] else "",
                "да" if row["deleted"] else "",
            ]
        )
    if len(sheet) == 1:
        sheet.append([("В 1С нет номенклатуры", "text")])
    content = write_book([("Номенклатура", sheet)])
    name = "номенклатура-1с.xlsx"
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f"attachment; filename=nomenclature.xlsx; filename*=UTF-8''{quote(name)}"
    return response


@roles_required("receiver", "warehouse")
def warehouse_home(request):
    return redirect("/reports")


@roles_required("receiver", "warehouse")
def stock(request):
    built = request.GET.get("run") == "1"
    product = (request.GET.get("product") or "").strip()
    supplier = (request.GET.get("supplier") or "").strip()
    rows = stock_rows() if built else []
    if product:
        needle = product.casefold()
        rows = [row for row in rows if needle in row["product"].casefold()]
    if supplier:
        needle = supplier.casefold()
        rows = [row for row in rows if needle in row["supplier"].casefold()]
    return render(
        request,
        "stock.html",
        {"built": built, "rows": rows, "product": product, "supplier": supplier},
    )


@roles_required("receiver", "warehouse")
def stock_lot(request, lot_id):
    lot = get_object_or_404(
        Lot.objects.select_related("product", "caliber", "grade", "supplier", "receipt"),
        pk=lot_id,
    )
    product = (request.GET.get("product") or "").strip()
    supplier = (request.GET.get("supplier") or "").strip()
    back = "/stock?run=1"
    if product or supplier:
        back += f"&product={quote(product)}&supplier={quote(supplier)}"
    return render(
        request,
        "stock_lot.html",
        {
            "lot": lot,
            "balance": Balance.objects.filter(lot_id=lot.id).first(),
            "lines": lot_reserve_rows(lot.id),
            "journal": lot_journal(lot.id),
            "back": back,
        },
    )


def _contains(value: str, needle: str) -> bool:
    return needle.casefold() in (value or "").casefold()


def _demand_query(request):
    if "date_from" not in request.GET and "date_to" not in request.GET:
        today = timezone.localdate()
        date_from_raw = today.isoformat()
        date_to_raw = (today + timedelta(days=7)).isoformat()
    else:
        date_from_raw = request.GET.get("date_from") or ""
        date_to_raw = request.GET.get("date_to") or ""
    product = (request.GET.get("product") or "").strip()
    party = (request.GET.get("party") or "").strip()
    date_from = query_date(date_from_raw)
    date_to = query_date(date_to_raw)

    def line_visible(row) -> bool:
        if date_from and row["ship_date"] < date_from:
            return False
        if date_to and row["ship_date"] > date_to:
            return False
        if product and not _contains(row["product"], product) and not _contains(row["spec"], product):
            return False
        if party and not _contains(row["consignee"], party):
            return False
        return True

    def inbound_visible(row) -> bool:
        receipt = row["receipt_date"]
        if date_from or date_to:
            if receipt is None:
                return False
            if date_from and receipt < date_from:
                return False
            if date_to and receipt > date_to:
                return False
        if product and not _contains(row["product"], product):
            return False
        return True

    return {
        "date_from": date_from_raw,
        "date_to": date_to_raw,
        "product": product,
        "party": party,
        "filtered": bool(date_from or date_to or product or party),
        "line_visible": line_visible,
        "inbound_visible": inbound_visible,
    }


def _id_set(values) -> set[int]:
    chosen = set()
    for raw in values:
        try:
            chosen.add(int(raw))
        except (TypeError, ValueError):
            continue
    return chosen


def _flag(values, index) -> bool:
    return index < len(values) and values[index] == "1"


def _demand_grouping(request):
    known = {key for key, _label in GROUP_FIELDS}
    labels = dict(GROUP_FIELDS)
    if request.GET.get("group_set") == "1":
        keys = request.GET.getlist("group_by")
        ons = request.GET.getlist("group_on")
        rows = []
        seen = set()
        for index, key in enumerate(keys):
            if key not in known or key in seen:
                continue
            seen.add(key)
            rows.append({"key": key, "label": labels[key], "on": True if not ons else _flag(ons, index)})
    else:
        legacy = request.GET.get("group") or "product"
        if legacy == "client":
            legacy = "consignee"
        if legacy not in known:
            legacy = "product"
        rows = [{"key": legacy, "label": labels[legacy], "on": True}]
    return rows, [row["key"] for row in rows if row["on"]]


def _demand_columns(request):
    labels = {key: label for key, label, _num in DEMAND_COLUMNS}
    nums = {key for key, _label, num in DEMAND_COLUMNS if num}
    if request.GET.get("col_set") == "1":
        keys = request.GET.getlist("col")
        ons = request.GET.getlist("col_on")
        rows = []
        seen = set()
        for index, key in enumerate(keys):
            if key not in labels or key in seen:
                continue
            seen.add(key)
            rows.append({"key": key, "label": labels[key], "num": key in nums, "on": _flag(ons, index)})
    else:
        rows = [
            {"key": key, "label": label, "num": num, "on": key in DEFAULT_COLUMNS}
            for key, label, num in DEMAND_COLUMNS
        ]
    visible = []
    labeled = False
    for row in rows:
        if not row["on"]:
            continue
        item = dict(row)
        item["total_label"] = not item["num"] and not labeled
        if item["total_label"]:
            labeled = True
        visible.append(item)
    return rows, visible


def _demand_picks(request):
    fields = {key for key, _label in PICK_FIELDS}
    ops = {key for key, _label in PICK_OPS}
    labels = dict(PICK_FIELDS)
    op_labels = dict(PICK_OPS)
    if request.GET.get("pick_set") != "1":
        return []
    rows = []
    keys = request.GET.getlist("pick_field")
    operators = request.GET.getlist("pick_op")
    values = request.GET.getlist("pick_value")
    ons = request.GET.getlist("pick_on")
    for index, key in enumerate(keys):
        if key not in fields:
            continue
        raw_op = operators[index] if index < len(operators) else "equals"
        raw_op = OP_ALIAS.get(raw_op, raw_op)
        op = raw_op if raw_op in ops else "equals"
        rows.append(
            {
                "field": key,
                "label": labels[key],
                "op": op,
                "op_label": op_labels[op],
                "value": values[index] if index < len(values) else "",
                "on": _flag(ons, index),
            }
        )
    return rows


class _Down:
    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return self.value > other.value


def _raw(row, field):
    key = VALUE_KEYS.get(field, field)
    if key not in row and field == "ship_date":
        return row.get("receipt_date")
    return row.get(key) if key in row else None


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _texts(value) -> tuple[str, ...]:
    if _blank(value):
        return ("",)
    if isinstance(value, datetime):
        value = value.date()
    if hasattr(value, "strftime"):
        return (value.strftime("%d.%m.%Y"), value.isoformat())
    if isinstance(value, Decimal):
        plain = format(value, "f")
        shown = f"{value:.3f}"
        return (plain, plain.replace(".", ","), shown, shown.replace(".", ","))
    return (str(value),)


def _number(text):
    try:
        return Decimal(str(text).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _day(text):
    text = str(text).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _equals(value, needle: str) -> bool:
    if isinstance(value, Decimal):
        number = _number(needle)
        return number is not None and value == number
    if hasattr(value, "strftime"):
        day = _day(needle)
        current = value.date() if isinstance(value, datetime) else value
        return day is not None and current == day
    return any(item.casefold() == needle.casefold() for item in _texts(value))


def _contains_text(value, needle: str) -> bool:
    folded = needle.casefold()
    return any(folded in item.casefold() for item in _texts(value))


def _ordered(value, needle: str):
    if isinstance(value, Decimal):
        number = _number(needle)
        return None if number is None else (value, number)
    if hasattr(value, "strftime"):
        day = _day(needle)
        current = value.date() if isinstance(value, datetime) else value
        return None if day is None else (current, day)
    return (str(value or "").casefold(), needle.casefold())


def _compare(value, op, needle: str) -> bool:
    op = OP_ALIAS.get(op, op)
    if op == "is_empty":
        return _blank(value)
    if op == "is_not_empty":
        return not _blank(value)
    if op == "equals":
        return _equals(value, needle)
    if op == "not_equals":
        return not _equals(value, needle)
    if op == "not_contains":
        return not _contains_text(value, needle)
    if op == "starts_with":
        return any(item.casefold().startswith(needle.casefold()) for item in _texts(value))
    if op == "ends_with":
        return any(item.casefold().endswith(needle.casefold()) for item in _texts(value))
    if op in ("in_list", "not_in_list"):
        items = [line.strip() for line in needle.splitlines() if line.strip()]
        found = any(_equals(value, item) for item in items) if items else False
        return found if op == "in_list" else not found
    pair = _ordered(value, needle)
    if pair is None:
        return False
    left, right = pair
    if op == "greater":
        return left > right
    if op == "less":
        return left < right
    if op == "greater_or_equal":
        return left >= right
    if op == "less_or_equal":
        return left <= right
    return _contains_text(value, needle)


def _pick_live(pick) -> bool:
    if not pick["on"]:
        return False
    if pick["op"] in ("is_empty", "is_not_empty"):
        return True
    if pick["op"] in ("equals", "not_equals"):
        return True
    return bool(str(pick["value"]).strip())


def _picks_ok(row, picks, allowed=None) -> bool:
    for pick in picks:
        if not _pick_live(pick):
            continue
        if allowed is not None and pick["field"] not in allowed:
            continue
        key = VALUE_KEYS.get(pick["field"], pick["field"])
        if key not in row and not (pick["field"] == "ship_date" and "receipt_date" in row):
            continue
        if not _compare(_raw(row, pick["field"]), pick["op"], str(pick["value"])):
            return False
    return True


def _norm(value):
    if _blank(value):
        return (0, "")
    if isinstance(value, datetime):
        value = value.date()
    if hasattr(value, "toordinal"):
        return (1, value.toordinal())
    if isinstance(value, Decimal):
        return (2, value)
    return (3, str(value).casefold())


def _ordering(sorts):
    active = [item for item in sorts if item["on"]]
    if not active:
        return None

    def key(row):
        parts = []
        for item in active:
            rank, raw = _norm(_raw(row, item["field"]))
            if item["desc"] and isinstance(raw, (int, Decimal)):
                parts.append((rank, -raw))
            elif item["desc"]:
                parts.append((rank, _Down(raw)))
            else:
                parts.append((rank, raw))
        return tuple(parts)

    return key


def _demand_sorts(request):
    labels = {key: label for key, label, _num in DEMAND_COLUMNS}
    if request.GET.get("sort_set") != "1":
        return []
    rows = []
    keys = request.GET.getlist("sort_by")
    directions = request.GET.getlist("sort_dir")
    ons = request.GET.getlist("sort_on")
    seen = set()
    for index, key in enumerate(keys):
        if key not in labels or key in seen:
            continue
        seen.add(key)
        direction = directions[index] if index < len(directions) and directions[index] in ("asc", "desc") else "asc"
        rows.append(
            {
                "field": key,
                "label": labels[key],
                "dir": direction,
                "on": _flag(ons, index),
                "desc": direction == "desc",
            }
        )
    return rows


def _demand_categories(request):
    catalog = list(ProductGroup.objects.order_by("name"))
    names = {item.id: item.name for item in catalog}
    names[0] = "Без группы"
    marked = request.GET.get("category_set") == "1"
    chosen = _id_set(request.GET.getlist("category")) if marked else set(names)
    rows = [{"id": item.id, "name": item.name, "on": item.id in chosen} for item in catalog]
    rows.append({"id": 0, "name": "Без группы", "on": 0 in chosen})
    return rows, chosen if marked else None


_LINE_KG = {
    "ordered": "ordered_kg",
    "picked": "picked_kg",
    "reserve": "reserve_kg",
    "free": "free_kg",
    "blocked": "blocked_kg",
    "stock": "stock_kg",
    "incoming": "incoming_kg",
    "short": "short_kg",
}


def _demand_line_cell(row, column):
    key = column["key"]
    if key == "order":
        return row["order_number"] or ""
    if key == "ship_date":
        return row["ship_date"]
    if key in _LINE_KG:
        return row[_LINE_KG[key]]
    return row.get(key) or ""


def _demand_sum_cell(source, column, text_style, num_style):
    if column.get("total_label"):
        return source["title"], text_style
    field = _LINE_KG.get(column["key"])
    if field:
        return source[field], num_style
    return None, text_style


def _demand_order_rows(context) -> list[list]:
    columns = context["columns"]
    header = [(column["label"], "header") for column in columns] or [("Поля", "header")]
    rows = [header]

    def walk(nodes):
        for item in nodes:
            if item["title"]:
                titled = {**item, "title": ("  " * item["level"]) + item["title"]}
                rows.append(
                    [_demand_sum_cell(titled, column, "group", "group_num") for column in columns]
                    or [(item["title"], "group")]
                )
            walk(item["children"])
            for line in item["rows"]:
                rows.append([_demand_line_cell(line, column) for column in columns])

    groups = context["groups"]
    if groups:
        walk(groups)
        totals = {
            "title": "Итого",
            "ordered_kg": context["ordered_total"],
            "picked_kg": context["picked_total"],
            "reserve_kg": context["reserve_total"],
            "free_kg": context["free_total"],
            "blocked_kg": context["blocked_total"],
            "stock_kg": context["stock_total"],
            "incoming_kg": context["incoming_total"],
            "short_kg": context["short_total"],
        }
        rows.append([_demand_sum_cell(totals, column, "total", "total_num") for column in columns] or [("Итого", "total")])
    else:
        if context["filtered"]:
            message = "Ничего не найдено."
        elif context["has_orders"]:
            message = "Нехватки нет."
        else:
            message = "Открытых заказов нет."
        rows.append([(message, "text")])
    return rows


def _demand_inbound_rows(context) -> list[list]:
    header = [
        ("Заказ", "header"),
        ("Поставщик", "header"),
        ("Поступление", "header"),
        ("Товар", "header"),
        ("Калибр", "header"),
        ("Сортность", "header"),
        ("Заказано", "header"),
        ("Принято", "header"),
        ("Осталось", "header"),
    ]
    rows = [header]
    inbound = context["inbound"]
    if not inbound:
        if context["date_from"] or context["date_to"] or context["product"]:
            message = "Ничего не найдено."
        else:
            message = "Нет заказов, которые ещё нужно принять."
        rows.append([(message, "text")])
        return rows
    for row in inbound:
        rows.append(
            [
                row["order_number"] or "",
                row["supplier"] or "",
                row["receipt_date"],
                row["product"] or "",
                row["caliber"] or "",
                row["grade"] or "",
                row["ordered_kg"],
                row["received_kg"],
                row["left_kg"],
            ]
        )
    rows.append(
        [
            ("Итого к приёмке", "total"),
            ("", "total"),
            ("", "total"),
            ("", "total"),
            ("", "total"),
            ("", "total"),
            ("", "total"),
            ("", "total"),
            (context["inbound_total"], "total_num"),
        ]
    )
    return rows


def _demand_xlsx(context) -> HttpResponse:
    content = write_book(
        [
            ("Под заказы", _demand_order_rows(context)),
            ("К приёмке", _demand_inbound_rows(context)),
        ]
    )
    start = context["date_from"] or ""
    end = context["date_to"] or ""
    name = f"потребность-{start}-{end}.xlsx" if start or end else "потребность.xlsx"
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f"attachment; filename=demand.xlsx; filename*=UTF-8''{quote(name)}"
    return response


@roles_required("receiver", "warehouse")
def demand(request):
    chosen = request.GET.getlist("all")
    show_all = "1" in chosen if chosen else True
    grouping, modes = _demand_grouping(request)
    column_rows, columns = _demand_columns(request)
    picks = _demand_picks(request)
    sorts = _demand_sorts(request)
    categories, category_ids = _demand_categories(request)
    export = request.GET.get("export") == "xlsx"
    built = request.GET.get("run") == "1" or export
    filters = _demand_query(request)
    picks_on = any(_pick_live(pick) for pick in picks)
    ordering = _ordering(sorts)
    context = {
        "groups": [],
        "show_all": show_all,
        "grouping": grouping,
        "column_rows": column_rows,
        "columns": columns,
        "picks": picks,
        "sorts": sorts,
        "pick_fields": [{"key": key, "label": label} for key, label in PICK_FIELDS],
        "pick_ops": [{"key": key, "label": label} for key, label in PICK_OPS],
        "group_fields": [{"key": key, "label": label} for key, label in GROUP_FIELDS],
        "column_fields": [{"key": key, "label": label} for key, label, _num in DEMAND_COLUMNS],
        "categories": categories,
        "built": built,
        "has_orders": False,
        "ordered_total": Decimal("0"),
        "picked_total": Decimal("0"),
        "reserve_total": Decimal("0"),
        "free_total": Decimal("0"),
        "blocked_total": Decimal("0"),
        "stock_total": Decimal("0"),
        "incoming_total": Decimal("0"),
        "short_total": Decimal("0"),
        "inbound": [],
        "inbound_total": Decimal("0"),
        "date_from": filters["date_from"],
        "date_to": filters["date_to"],
        "product": filters["product"],
        "party": filters["party"],
        "filtered": filters["filtered"] or picks_on or (category_ids is not None and category_ids != {item["id"] for item in categories}),
    }
    if not built:
        return render(request, "demand.html", context)
    report = demand_report()
    lines = report["lines"] if show_all else [row for row in report["lines"] if row["short_kg"] > 0]
    lines = [row for row in lines if filters["line_visible"](row) and _picks_ok(row, picks)]
    inbound = [row for row in report["inbound"] if filters["inbound_visible"](row) and _picks_ok(row, picks, INBOUND_PICKS)]
    if ordering:
        inbound = sorted(inbound, key=ordering)
    if category_ids is not None:
        lines = [row for row in lines if row["category_id"] in category_ids]
        inbound = [row for row in inbound if row["category_id"] in category_ids]
    context.update(
        {
            "groups": group_demand(lines, modes, ordering),
            "has_orders": report["has_orders"],
            "ordered_total": as_kg(sum((row["ordered_kg"] for row in lines), Decimal("0"))),
            "picked_total": as_kg(sum((row["picked_kg"] for row in lines), Decimal("0"))),
            "reserve_total": as_kg(sum((row["reserve_kg"] for row in lines), Decimal("0"))),
            "free_total": as_kg(sum((row["free_kg"] for row in lines), Decimal("0"))),
            "blocked_total": as_kg(sum((row["blocked_kg"] for row in lines), Decimal("0"))),
            "stock_total": as_kg(sum((row["stock_kg"] for row in lines), Decimal("0"))),
            "incoming_total": as_kg(sum((row["incoming_kg"] for row in lines), Decimal("0"))),
            "short_total": as_kg(sum((row["short_kg"] for row in lines), Decimal("0"))),
            "inbound": inbound,
            "inbound_total": as_kg(sum((row["left_kg"] for row in inbound), Decimal("0"))),
        }
    )
    if export:
        return _demand_xlsx(context)
    return render(request, "demand.html", context)
