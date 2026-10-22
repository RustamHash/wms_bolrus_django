from decimal import Decimal

from apps.common.present import format_kg, group_units, unit_lines

# Встроенный шрифт TSC «3»: 16×24 точки до увеличения.
FONT = "3"
FONT_WIDTH = 16
FONT_HEIGHT = 24

# Ширина, высота и зазор, мм. Свой размер у каждого документа.
# Зазор 0: длина берётся из высоты этого задания, датчик зазора не переключается.
LABEL_MM = {
    "unit": (58, 40, 0),
    "pallet": (100, 70, 0),
    "shipment": (100, 70, 0),
    "test": (100, 70, 0),
}


def mm_to_dots(mm: int, dpi: int) -> int:
    return max(1, int(round(mm / 25.4 * dpi)))


def _clean(value: str) -> str:
    return str(value).replace('"', "'").replace("\r", " ").replace("\n", " ").strip()


def _wrap(text: str, max_chars: int) -> list[str]:
    text = _clean(text)
    if not text:
        return []
    max_chars = max(1, max_chars)
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        trial = word if not current else f"{current} {word}"
        if len(trial) <= max_chars:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _label(lines: list[str], barcode: str, label_w: int, label_h: int) -> str:
    barcode_h = 64
    text_bottom = label_h - barcode_h - 28
    if text_bottom < FONT_HEIGHT + 16:
        text_bottom = max(FONT_HEIGHT + 16, label_h - 8)
        barcode_y = None
    else:
        barcode_y = text_bottom + 6

    scale = 2
    step = FONT_HEIGHT * scale + 4
    max_chars = max(1, (label_w - 40) // (FONT_WIDTH * scale))
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap(line, max_chars))
    if not wrapped:
        wrapped = [""]
    if 16 + len(wrapped) * step > text_bottom:
        scale = 1
        step = FONT_HEIGHT + 4
        max_chars = max(1, (label_w - 40) // FONT_WIDTH)
        wrapped = []
        for line in lines:
            wrapped.extend(_wrap(line, max_chars))
        if not wrapped:
            wrapped = [""]

    commands = ["CLS\r\n", "CODEPAGE 1251\r\n"]
    y = 16
    for line in wrapped:
        if y + FONT_HEIGHT * scale > text_bottom:
            break
        commands.append(f'TEXT 20,{y},"{FONT}",0,{scale},{scale},"{line}"\r\n')
        y += step
    if barcode_y is not None and barcode:
        commands.append(f'BARCODE 16,{barcode_y},"128",{barcode_h},1,0,2,2,"{_clean(barcode)}"\r\n')
    commands.append("PRINT 1,1\r\n")
    return "".join(commands)


def _job(printer, kind: str, labels: list[tuple[list[str], str]]) -> bytes:
    width_mm, height_mm, gap_mm = LABEL_MM[kind]
    dpi = int(printer.dpi or 203)
    label_w = mm_to_dots(width_mm, dpi)
    label_h = mm_to_dots(height_mm, dpi)
    parts = [
        f"SIZE {width_mm} mm,{height_mm} mm\r\n",
        f"GAP {gap_mm} mm,0 mm\r\n",
        "DIRECTION 1\r\n",
    ]
    for lines, barcode in labels:
        parts.append(_label(lines, barcode, label_w, label_h))
    return "".join(parts).encode("cp1251", errors="replace")


def unit_label(printer, unit) -> bytes:
    return _job(printer, "unit", [(unit_lines(unit), unit.barcode)])


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
    return _job(printer, "pallet", [(chunk, pallet.barcode) for chunk in chunks])


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
    return _job(printer, "shipment", [(lines, f"SH{shipment.id:06d}")])


def test_label(printer) -> bytes:
    return _job(printer, "test", [(["WMS OK", "Проверка"], "")])
