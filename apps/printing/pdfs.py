from io import BytesIO

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from apps.common.fonts import find_font
from apps.common.present import format_kg, group_units

_font_ready = False


def ensure_font() -> None:
    global _font_ready
    if not _font_ready:
        pdfmetrics.registerFont(TTFont("WmsFont", find_font()))
        _font_ready = True


class Sheet:
    def __init__(self, pagesize):
        self.buffer = BytesIO()
        self.pagesize = pagesize
        self.canvas = canvas.Canvas(self.buffer, pagesize=pagesize)
        ensure_font()
        self.width, self.height = pagesize
        self.y = self.height - 15 * mm

    def page_break(self) -> None:
        if self.y < 25 * mm:
            self.canvas.showPage()
            ensure_font()
            self.y = self.height - 15 * mm

    def text(self, value: str, size: int = 11, gap: float | None = None) -> None:
        self.page_break()
        self.canvas.setFont("WmsFont", size)
        self.canvas.drawString(15 * mm, self.y, value[:180])
        self.y -= gap or (size + 4)

    def barcode(self, value: str) -> None:
        self.page_break()
        barcode = code128.Code128(value, barHeight=12 * mm, barWidth=0.35 * mm, humanReadable=True)
        barcode.drawOn(self.canvas, 15 * mm, self.y - 16 * mm)
        self.y -= 22 * mm

    def finish(self) -> bytes:
        self.canvas.save()
        return self.buffer.getvalue()


def unit_label_pdf(unit) -> bytes:
    sheet = Sheet((100 * mm, 70 * mm))
    sheet.barcode(unit.barcode)
    for line in (
        unit.lot.product.name,
        f"{unit.lot.caliber.name} · {unit.grade.name}",
        f"{format_kg(unit.net_kg)} кг · {unit.packaging.name}",
        f"{unit.lot.supplier.name} · {unit.lot.received_on.strftime('%d.%m.%Y')}",
    ):
        sheet.text(line, 10, 12)
    return sheet.finish()


def pallet_passport_pdf(pallet) -> bytes:
    from apps.printing.passport import render_pallet_passport_pdf

    return render_pallet_passport_pdf(pallet)


def sticker_pdf(shipment, units) -> bytes:
    groups = group_units(units)
    sheet = Sheet(A5)
    sheet.text(shipment.seller_name, 16, 18)
    for line in (shipment.seller_requisites or "").splitlines():
        if line.strip():
            sheet.text(line.strip(), 10, 12)
    sheet.text(f"Клиент: {shipment.client_name}", 12, 16)
    sheet.text(f"Накладная {shipment.number}", 12, 16)
    sheet.barcode(f"SH{shipment.id:06d}")
    total = 0
    for group in groups:
        sheet.text(
            f"{group.product} {group.caliber} {group.grade} — {format_kg(group.kg)} кг",
            10,
            13,
        )
        total += group.kg
    sheet.text(f"Итого {format_kg(total)} кг", 12, 16)
    return sheet.finish()


def waybill_pdf(shipment, units) -> bytes:
    groups = group_units(units)
    sheet = Sheet(A4)
    sheet.text(f"Отгрузочный лист {shipment.number}", 16, 20)
    sheet.text(f"Заказ {shipment.order.number} от {shipment.order.ship_date.strftime('%d.%m.%Y')}")
    sheet.text(f"Клиент: {shipment.client_name}")
    sheet.text(f"Продавец: {shipment.seller_name}")
    sheet.text("Условия отгрузки:", 12, 16)
    for line in (shipment.shipping_terms or "Не указаны").splitlines() or ["Не указаны"]:
        sheet.text(line, 11, 14)
    sheet.text("Состав:", 12, 16)
    total = 0
    for group in groups:
        sheet.text(
            f"{group.product} · {group.caliber} · {group.grade}: {group.count} мест, {format_kg(group.kg)} кг",
            10,
            14,
        )
        total += group.kg
    sheet.text(f"Итого {format_kg(total)} кг", 12, 16)
    sheet.text("Условия отгрузки подтверждены при сборке.", 10, 16)
    return sheet.finish()


def test_pdf() -> bytes:
    sheet = Sheet(A5)
    sheet.text("Проверка сетевого принтера", 16, 20)
    sheet.text("WMS овощного склада")
    return sheet.finish()


def _wrap(value: str, size: int, width: float) -> list[str]:
    words = (value or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if pdfmetrics.stringWidth(trial, "WmsFont", size) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def _line_caliber(line) -> str:
    if line.caliber_id:
        return line.caliber.name
    order_line = line.order_line
    if order_line is None:
        return ""
    if order_line.caliber_mode == "range" and order_line.caliber_from_id and order_line.caliber_to_id:
        return f"{order_line.caliber_from.name}–{order_line.caliber_to.name}"
    return ""


def _line_grade(line) -> str:
    if line.grade_id:
        return line.grade.name
    return ""


def _task_title(task) -> str:
    titles = {"pick": "Задание на отбор", "ship": "Задание на отгрузку", "receive": "Задание на приёмку"}
    title = titles.get(task.kind, "Задание")
    return f"{title} {task.number or task.id}"


def _product_group_name(line) -> str:
    product = line.product
    if product is not None and product.group_id:
        return product.group.name
    return "Без группы"


def _lines_by_product_group(lines) -> list[tuple[str, list]]:
    buckets: dict[str, list] = {}
    order: list[str] = []
    for line in lines:
        title = _product_group_name(line)
        if title not in buckets:
            buckets[title] = []
            order.append(title)
        buckets[title].append(line)
    order.sort(key=lambda name: (name == "Без группы", name.casefold()))
    return [(title, buckets[title]) for title in order]


def tasks_pdf(tasks, *, by_groups: bool = False) -> bytes:
    from apps.common.constants import TASK_STATUS

    ensure_font()
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left = 12 * mm
    right = width - 12 * mm
    columns = (
        ("№", 10 * mm),
        ("Номенклатура", 72 * mm),
        ("Калибр", 28 * mm),
        ("Сортность", 26 * mm),
        ("План, кг", 24 * mm),
        ("Отобрано, кг", 26 * mm),
    )
    for index, task in enumerate(tasks):
        if index:
            pdf.showPage()
        y = _task_sheet(pdf, task, left, right, height, columns, TASK_STATUS)
        _task_lines(pdf, task, y, left, right, height, columns, by_groups=by_groups)
    pdf.save()
    return buffer.getvalue()


def _text(pdf, x, y, value: str, size: int = 11) -> float:
    pdf.setFont("WmsFont", size)
    pdf.drawString(x, y, value)
    return y - (size + 4)


def _task_sheet(pdf, task, left, right, height, columns, statuses) -> float:
    y = height - 14 * mm
    pdf.setFont("WmsFont", 16)
    pdf.drawString(left, y, _task_title(task)[:90])
    y -= 8 * mm
    status = statuses.get(task.status, task.status)
    y = _text(pdf, left, y, f"Статус: {status}", 10)
    order = task.order
    purchase = task.purchase_order
    if order is not None:
        number = order.number or ""
        y = _text(pdf, left, y, f"Заказ {number}".strip())
        y = _text(pdf, left, y, f"Дата отгрузки: {order.ship_date.strftime('%d.%m.%Y')}")
        if order.consignee_id:
            y = _text(pdf, left, y, f"Грузополучатель: {order.consignee.name}"[:140])
        y = _text(pdf, left, y, f"Продавец: {order.seller.name}"[:140])
        terms = (order.shipping_terms or "").strip()
        if terms:
            y = _text(pdf, left, y, "Условия отгрузки:", 11)
            for part in terms.splitlines():
                for line in _wrap(part, 10, right - left):
                    y = _text(pdf, left, y, line, 10)
    elif purchase is not None:
        supplier = task.supplier.name if task.supplier_id else purchase.supplier.name
        y = _text(pdf, left, y, f"Заказ поставщику {purchase.number or ''}".strip())
        y = _text(pdf, left, y, f"Поставщик: {supplier}"[:140])
        if task.ttn_number:
            y = _text(pdf, left, y, f"ТТН {task.ttn_number}")
        if task.doc_date:
            y = _text(pdf, left, y, f"Дата прихода: {task.doc_date.strftime('%d.%m.%Y')}")
    y -= 2 * mm
    return y


def _row(pdf, y, left, cells, widths, size: int, numeric: set[int]) -> float:
    pad = 1.6 * mm
    line_h = size + 2
    wrapped = []
    for index, (value, width) in enumerate(zip(cells, widths)):
        wrapped.append(_wrap(str(value), size, width - 2 * pad))
    height = max(len(item) for item in wrapped) * line_h + 2 * pad
    x = left
    pdf.setFont("WmsFont", size)
    for index, (lines, width) in enumerate(zip(wrapped, widths)):
        pdf.rect(x, y - height, width, height, stroke=1, fill=0)
        cursor = y - pad - size
        for line in lines:
            if index in numeric:
                pdf.drawRightString(x + width - pad, cursor, line)
            else:
                pdf.drawString(x + pad, cursor, line)
            cursor -= line_h
        x += width
    return y - height


def _task_lines(pdf, task, y, left, right, height, columns, *, by_groups: bool = False) -> None:
    widths = [item[1] for item in columns]
    titles = [item[0] for item in columns]
    bottom = 18 * mm
    table_width = sum(widths)

    def header(top: float) -> float:
        return _row(pdf, top, left, titles, widths, 9, {4, 5})

    def ensure(top: float, need: float) -> float:
        if top - need >= bottom:
            return top
        pdf.showPage()
        top = height - 14 * mm
        top = _text(pdf, left, top, f"{task.number or task.id}, продолжение", 12)
        top -= 2 * mm
        return header(top)

    if y < bottom + 20 * mm:
        pdf.showPage()
        y = height - 14 * mm
        y = _text(pdf, left, y, f"{task.number or task.id}, продолжение", 12)
        y -= 2 * mm
    y = header(y)
    lines = list(task.lines.all())
    sections = _lines_by_product_group(lines) if by_groups else [("", lines)]
    total = 0
    number = 0
    for group_title, group_lines in sections:
        if by_groups and group_title:
            y = ensure(y, 10 * mm)
            y -= 2 * mm
            pdf.setFont("WmsFont", 10)
            pdf.drawString(left + 1.6 * mm, y - 8, group_title[:90])
            pdf.rect(left, y - 10, table_width, 10, stroke=1, fill=0)
            y -= 10
        for line in group_lines:
            number += 1
            cells = [
                str(number),
                line.product.name,
                _line_caliber(line),
                _line_grade(line),
                format_kg(line.qty_kg),
                "",
            ]
            y = ensure(y, 8 * mm)
            y = _row(pdf, y, left, cells, widths, 9, {0, 4, 5})
            total += line.qty_kg
    if not lines:
        y = _text(pdf, left, y - 4 * mm, "Строк нет")
    else:
        y -= 8 * mm
        y = _text(pdf, left, y, f"Итого план {format_kg(total)} кг", 11)
    if y < bottom + 16 * mm:
        pdf.showPage()
        y = height - 14 * mm
    y -= 8 * mm
    _text(pdf, left, y, "Отобрал ____________________", 11)
    _text(pdf, left + 90 * mm, y, "Дата ______________", 11)


def route_sheet_pdf(route, stops, weights: dict) -> bytes:
    return routes_sheets_pdf([(route, stops, weights)])


def routes_sheets_pdf(sheets: list) -> bytes:
    """Несколько маршрутных листов в одном PDF (каждый маршрут с новой страницы)."""
    from django.utils import timezone

    if not sheets:
        raise ValueError("Нет маршрутов для печати")

    ensure_font()
    buffer = BytesIO()
    page = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=page)
    width, height = page
    left = 12 * mm
    columns = (
        ("№", 12 * mm),
        ("Заказ", 36 * mm),
        ("Грузополучатель", 70 * mm),
        ("Адрес", 90 * mm),
        ("Зона", 36 * mm),
        ("Кг", 22 * mm),
    )
    widths = [item[1] for item in columns]
    bottom = 18 * mm

    for index, (route, stops, weights) in enumerate(sheets):
        if index:
            pdf.showPage()
        y = height - 14 * mm
        title = route.name or f"Маршрут {route.id}"
        pdf.setFont("WmsFont", 16)
        pdf.drawString(left, y, f"Маршрутный лист · {title}"[:90])
        y -= 8 * mm
        y = _text(pdf, left, y, f"Дата: {route.route_date.strftime('%d.%m.%Y')}")
        driver_name = route.driver.full_name if route.driver_id else "—"
        y = _text(pdf, left, y, f"Водитель: {driver_name}"[:140])
        y = _text(pdf, left, y, f"Автомобиль: {route.vehicle_text or '—'}"[:140])
        if route.planned_load_at:
            load_at = timezone.localtime(route.planned_load_at)
            if load_at.date() != route.route_date:
                y = _text(pdf, left, y, f"Погрузка: {load_at.strftime('%d.%m.%Y %H:%M')}")
            else:
                y = _text(pdf, left, y, f"Погрузка: {load_at.strftime('%H:%M')}")
        else:
            y = _text(pdf, left, y, "Погрузка: не назначена")
        y = _text(pdf, left, y, f"Статус: {route.get_status_display()}")
        notes = (route.notes or "").strip()
        if notes:
            y = _text(pdf, left, y, "Комментарий:", 11)
            for part in notes.splitlines() or [notes]:
                for line in _wrap(part, 10, width - 24 * mm):
                    y = _text(pdf, left, y, line, 10)
        y -= 2 * mm

        def header(top: float) -> float:
            return _row(pdf, top, left, [item[0] for item in columns], widths, 9, {0, 5})

        def ensure(top: float, need: float) -> float:
            if top - need >= bottom:
                return top
            pdf.showPage()
            top = height - 14 * mm
            top = _text(pdf, left, top, f"{title}, продолжение", 12)
            top -= 2 * mm
            return header(top)

        y = header(y)
        total = 0
        for stop in stops:
            order = stop.order
            consignee = order.consignee
            zone = ""
            address = ""
            consignee_name = ""
            if consignee is not None:
                consignee_name = consignee.name
                address = consignee.address or ""
                zone = consignee.zone.name if consignee.zone_id else "Без зоны"
            else:
                zone = "Без зоны"
            weight = weights.get(order.id) or 0
            total += weight
            cells = [
                str(stop.position),
                order.number or str(order.id),
                consignee_name,
                address,
                zone,
                format_kg(weight),
            ]
            y = ensure(y, 8 * mm)
            y = _row(pdf, y, left, cells, widths, 8, {0, 5})
        y -= 8 * mm
        if not stops:
            y = _text(pdf, left, y, "Остановок нет")
        else:
            y = _text(pdf, left, y, f"Итого {format_kg(total)} кг · остановок {len(stops)}", 11)
        y -= 6 * mm
        _text(pdf, left, y, "Водитель ____________________", 11)
        _text(pdf, left + 90 * mm, y, "Склад ______________", 11)

    pdf.save()
    return buffer.getvalue()


def schedule_sheet_pdf(tree: dict) -> bytes:
    """Печать графика таблицей: план | факт | водитель | маршрут | комментарий | вес | ТТ."""
    ensure_font()
    buffer = BytesIO()
    page = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=page)
    width, height = page
    left = 12 * mm
    bottom = 16 * mm
    day = tree["day"]
    columns = (
        ("План", 36 * mm),
        ("Факт", 24 * mm),
        ("Водитель", 40 * mm),
        ("Маршрут", 52 * mm),
        ("Комментарий", 80 * mm),
        ("Вес, кг", 26 * mm),
        ("ТТ", 14 * mm),
    )
    widths = [item[1] for item in columns]
    titles = [item[0] for item in columns]
    y = height - 12 * mm
    pdf.setFont("WmsFont", 16)
    pdf.drawString(left, y, f"График отгрузки · {day.strftime('%d.%m.%Y')}")
    y -= 7 * mm
    y = _text(
        pdf,
        left,
        y,
        f"Маршрутов: {tree.get('route_count', 0)} · "
        f"Итого {format_kg(tree.get('total_weight', 0))} кг · ТТ {tree.get('total_tt', 0)}",
        10,
    )
    y -= 2 * mm

    def header(top: float) -> float:
        return _row(pdf, top, left, titles, widths, 9, {5, 6})

    def ensure(top: float, need: float) -> float:
        if top - need >= bottom:
            return top
        pdf.showPage()
        top = height - 12 * mm
        top = _text(pdf, left, top, f"График {day.strftime('%d.%m.%Y')}, продолжение", 12)
        top -= 2 * mm
        return header(top)

    rows = tree.get("rows") or []
    if not rows:
        y = _text(pdf, left, y, "Нет маршрутов с назначенным планом погрузки")
    else:
        y = header(y)
        for row in rows:
            cells = [
                row.get("plan") or "",
                row.get("fact") or "",
                row.get("label") or "",
                row.get("route") or "",
                row.get("notes") or "",
                format_kg(row.get("weight") or 0),
                str(row.get("tt_count") or 0),
            ]
            y = ensure(y, 8 * mm)
            y = _row(pdf, y, left, cells, widths, 9, {5, 6})
        y -= 4 * mm
        y = _text(
            pdf,
            left,
            y,
            f"Итого {format_kg(tree.get('total_weight', 0))} кг · ТТ {tree.get('total_tt', 0)}",
            11,
        )

    pdf.save()
    return buffer.getvalue()
