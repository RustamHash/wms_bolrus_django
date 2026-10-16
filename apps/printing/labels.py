from decimal import Decimal

from PIL import Image, ImageDraw, ImageFont

from apps.common.fonts import find_font
from apps.common.present import format_kg, group_units, unit_lines


def mm_to_dots(mm: int, dpi: int) -> int:
    return max(1, int(round(mm / 25.4 * dpi)))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_text(lines: list[str], width: int, height: int) -> Image.Image:
    image = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(image)
    font = None
    wrapped: list[str] = []
    size = 16
    for size in (30, 26, 22, 18, 16, 14):
        font = ImageFont.truetype(find_font(), size)
        wrapped = []
        for line in lines:
            wrapped.extend(_wrap(draw, line, font, width - 12))
        if len(wrapped) * (size + 4) <= height - 6:
            break
    y = 4
    for line in wrapped:
        if y + size > height:
            break
        draw.text((6, y), line, fill=0, font=font)
        y += size + 4
    return image


def bitmap_bytes(image: Image.Image) -> tuple[int, int, bytes]:
    image = image.convert("1")
    width, height = image.size
    width_bytes = (width + 7) // 8
    padded = Image.new("1", (width_bytes * 8, height), 1)
    padded.paste(image, (0, 0))
    pixels = padded.load()
    data = bytearray()
    for y in range(height):
        for xb in range(width_bytes):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                if pixels[x, y] == 0:
                    byte |= 1 << (7 - bit)
            data.append(byte)
    return width_bytes, height, bytes(data)


def _job(printer, labels: list[tuple[Image.Image, str]]) -> bytes:
    parts = [
        f"SIZE {printer.label_width_mm} mm,{printer.label_height_mm} mm\r\n".encode("ascii"),
        f"GAP {printer.gap_mm} mm,0 mm\r\n".encode("ascii"),
        f"DENSITY {int(printer.density)}\r\n".encode("ascii"),
        b"DIRECTION 1\r\n",
    ]
    for image, barcode in labels:
        width_bytes, height, data = bitmap_bytes(image)
        parts.append(b"CLS\r\n")
        parts.append(f"BITMAP 0,0,{width_bytes},{height},0,".encode("ascii") + data + b"\r\n")
        parts.append(
            f'BARCODE 16,{height + 6},"128",64,1,0,2,2,"{barcode}"\r\n'.encode("ascii")
        )
        parts.append(b"PRINT 1\r\n")
    return b"".join(parts)


def _text_image(printer, lines: list[str]) -> Image.Image:
    width = mm_to_dots(printer.label_width_mm, printer.dpi)
    height = max(40, mm_to_dots(printer.label_height_mm, printer.dpi) - 90)
    return render_text(lines, width, height)


def unit_label(printer, unit) -> bytes:
    return _job(printer, [(_text_image(printer, unit_lines(unit)), unit.barcode)])


def pallet_labels(printer, pallet) -> bytes:
    groups = group_units(pallet.units.all())
    lines = [pallet.number, "Вход" if pallet.kind == "inbound" else "Выход"]
    if pallet.kind == "inbound" and len(groups) == 1:
        group = groups[0]
        lines.extend(
            [
                group.product,
                f"{group.caliber} · {group.grade}",
                f"{group.count} мест · {format_kg(group.kg)} кг",
            ]
        )
    else:
        lines.append("Смешанная паллета" if pallet.kind == "outbound" else "Состав")
        for group in groups:
            lines.append(
                f"{group.product} {group.caliber} {group.grade} {group.count}шт {format_kg(group.kg)}кг"
            )
    if not groups:
        lines.append("Пусто")
    capacity = 8
    chunks = [lines[i : i + capacity] for i in range(0, len(lines), capacity)] or [lines]
    return _job(printer, [(_text_image(printer, chunk), pallet.barcode) for chunk in chunks])


def shipment_sticker(printer, shipment, groups) -> bytes:
    total = sum((group.kg for group in groups), start=Decimal("0"))
    lines = [
        shipment.seller_name,
        shipment.client_name,
        f"Накладная {shipment.number}",
        f"Нетто {format_kg(total)} кг",
    ]
    for group in groups[:6]:
        lines.append(f"{group.product} {group.caliber} {format_kg(group.kg)}кг")
    return _job(printer, [(_text_image(printer, lines), f"SH{shipment.id:06d}")])


def test_label(printer) -> bytes:
    return _job(printer, [(_text_image(printer, ["WMS", "Проверка печати TSC"]), "TEST1234")])
