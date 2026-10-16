from io import BytesIO

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4, A5
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
