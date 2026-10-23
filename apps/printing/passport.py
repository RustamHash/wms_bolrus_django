import os
from collections import OrderedDict
from io import BytesIO

import qrcode
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from apps.common.fonts import find_font
from apps.common.kg import as_kg, format_kg

# Лист A4, поля и таблица как в прежнем шаблоне: две одинаковые копии паспорта.
_PAGE_W, _PAGE_H = A4
_MARGIN_X = 10 * mm
_MARGIN_Y = 12 * mm
_GAP = 20 * mm
_LABEL_W = 55 * mm
_VALUE_W = 135 * mm
_ROW_H = 14.1 * mm
_QR_ROW_H = 36 * mm
_QR_SIZE = 32 * mm
_LABEL_SIZE = 18
_VALUE_SIZE = 22
_font_ready = False


def render_pallet_passport_pdf(pallet) -> bytes:
    units = list(
        pallet.units.exclude(status="rejected").select_related(
            "lot__product", "lot__caliber", "lot__supplier", "grade", "packaging"
        )
    )
    data = _context(pallet, units)
    rows = [
        ("Номер", data["number"]),
        ("Дата", data["date"]),
        ("ПОСТАВЩИК", data["supplier"]),
        ("Товар", data["product"]),
        ("Сортность", data["grade"]),
        ("Брутто / нетто", data["weight"]),
    ]
    if data["client"]:
        rows.append(("Клиент", data["client"]))
    qr = ImageReader(_write_qr(_qr_text(pallet, units)))
    row_h = _row_height(len(rows))
    block_h = len(rows) * row_h + _QR_ROW_H
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _ensure_font()
    top = _PAGE_H - _MARGIN_Y
    _draw_block(pdf, _MARGIN_X, top, rows, qr, row_h)
    _draw_block(pdf, _MARGIN_X, top - block_h - _GAP, rows, qr, row_h)
    pdf.save()
    return buffer.getvalue()


def _row_height(row_count: int) -> float:
    room = _PAGE_H - 2 * _MARGIN_Y - _GAP - 2 * _QR_ROW_H
    fitted = room / (2 * row_count)
    return min(_ROW_H, fitted)


def _ensure_font() -> None:
    global _font_ready
    if _font_ready:
        return
    regular = find_font()
    pdfmetrics.registerFont(TTFont("WmsPassport", regular))
    pdfmetrics.registerFont(TTFont("WmsPassport-Bold", _bold_font(regular)))
    _font_ready = True


def _bold_font(regular: str) -> str:
    folder = os.path.dirname(regular)
    names = {
        "arial.ttf": "arialbd.ttf",
        "calibri.ttf": "calibrib.ttf",
        "dejavusans.ttf": "DejaVuSans-Bold.ttf",
        "liberationsans-regular.ttf": "LiberationSans-Bold.ttf",
    }
    wanted = names.get(os.path.basename(regular).lower())
    if not wanted or not os.path.isdir(folder):
        return regular
    for entry in os.listdir(folder):
        if entry.lower() == wanted:
            return os.path.join(folder, entry)
    return regular


def _draw_block(pdf, x: float, top: float, rows: list[tuple[str, str]], qr, row_h: float) -> None:
    width = _LABEL_W + _VALUE_W
    height = len(rows) * row_h + _QR_ROW_H
    bottom = top - height
    pdf.setStrokeColorRGB(0, 0, 0)
    pdf.setLineWidth(0.6)
    pdf.rect(x, bottom, width, height)
    y = top
    for label, value in rows:
        y -= row_h
        pdf.line(x, y, x + width, y)
        pdf.line(x + _LABEL_W, y, x + _LABEL_W, y + row_h)
        _cell_text(pdf, x, y, _LABEL_W, row_h, label, _LABEL_SIZE)
        _cell_text(pdf, x + _LABEL_W, y, _VALUE_W, row_h, value, _VALUE_SIZE)
    pdf.line(x + _LABEL_W, bottom, x + _LABEL_W, y)
    qr_x = x + _LABEL_W + (_VALUE_W - _QR_SIZE) / 2
    qr_y = bottom + (_QR_ROW_H - _QR_SIZE) / 2
    pdf.drawImage(qr, qr_x, qr_y, _QR_SIZE, _QR_SIZE, mask="auto")


def _cell_text(pdf, x: float, y: float, width: float, height: float, text: str, size: int) -> None:
    pad = 2 * mm
    limit = max(1, width - 2 * pad)
    chosen = size
    while chosen > 9 and pdfmetrics.stringWidth(text, "WmsPassport-Bold", chosen) > limit:
        chosen -= 1
    pdf.setFont("WmsPassport-Bold", chosen)
    baseline = y + (height - chosen) / 2 + chosen * 0.2
    pdf.drawString(x + pad, baseline, text)


def _weights(units) -> tuple:
    gross = sum((as_kg(unit.gross_kg) for unit in units), start=0)
    net = sum((as_kg(unit.net_kg) for unit in units), start=0)
    return gross, net


def _context(pallet, units) -> dict:
    gross, net = _weights(units)
    return {
        "date": _unique(unit.lot.received_on.strftime("%d.%m.%Y") for unit in units) or _fallback_date(pallet),
        "supplier": _unique(unit.lot.supplier.name for unit in units),
        "product": _unique(_product(unit) for unit in units),
        "grade": _unique(unit.grade.name for unit in units),
        "weight": f"{format_kg(gross)} / {format_kg(net)} кг",
        "client": _client_name(pallet),
        "number": (pallet.number or "").strip() or "—",
    }


def passport_lines(pallet, units) -> list[str]:
    """Те же поля, что на листе паспорта, без подписей: на наклейке мало места."""
    data = _context(pallet, units)
    lines = [
        data["number"],
        data["date"],
        data["supplier"],
        data["product"],
        data["grade"],
        data["weight"],
    ]
    if data["client"]:
        lines.append(data["client"])
    return lines


def _client_name(pallet) -> str:
    if pallet.reserved_consignee_id:
        return (pallet.reserved_consignee.name or "").strip()
    if pallet.order_id and pallet.order.consignee_id:
        return (pallet.order.consignee.name or "").strip()
    return ""


def _product(unit) -> str:
    name = unit.lot.product.name
    if unit.lot.caliber_id:
        return f"{name}, {unit.lot.caliber.name}"
    return name


def _fallback_date(pallet) -> str:
    moment = pallet.closed_at or pallet.created_at or timezone.now()
    return timezone.localtime(moment).strftime("%d.%m.%Y")


def _unique(values) -> str:
    found = []
    for value in values:
        text = (value or "").strip()
        if text and text not in found:
            found.append(text)
    return ", ".join(found) or "—"


def _qr_text(pallet, units) -> str:
    lines = []
    if pallet.barcode:
        lines.append(pallet.barcode)
    if pallet.number:
        lines.append(pallet.number)
    buckets: OrderedDict[tuple, int] = OrderedDict()
    for unit in units:
        key = (unit.packaging.name, as_kg(unit.net_kg))
        buckets[key] = buckets.get(key, 0) + 1
    for (name, kg), count in buckets.items():
        lines.append(f"{name}: {count} x {format_kg(kg)} кг")
    gross, net = _weights(units)
    lines.append(f"Брутто {format_kg(gross)} кг")
    lines.append(f"Нетто {format_kg(net)} кг")
    return "\n".join(lines) or (pallet.number or pallet.barcode or str(pallet.pk))


def _write_qr(payload: str) -> BytesIO:
    image = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    image.add_data(payload)
    image.make(fit=True)
    buffer = BytesIO()
    image.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
