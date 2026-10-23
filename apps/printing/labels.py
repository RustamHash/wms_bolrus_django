from decimal import Decimal

from apps.common.present import format_kg, group_units, unit_lines

# Встроенный шрифт TSC «3»: 16×24 точки до увеличения.
FONT = "3"
FONT_WIDTH = 16
FONT_HEIGHT = 24

# Поле для вёрстки, мм. Высоту этикетки (SIZE) в принтер не шлём: она остаётся той,
# что настроена на TSC. Зазор нужен только чтобы найти разрез и не печатать на две наклейки.
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


def _label(lines: list[str], barcode: str, label_w: int, label_h: int, dpi: int, copies: int = 1) -> str:
    # 5 мм слева: печатающая головка TSC не начинает с самого края наклейки.
    left = mm_to_dots(5, dpi)
    top = mm_to_dots(3, dpi)
    right = mm_to_dots(4, dpi)
    bottom = mm_to_dots(3, dpi)
    gap = mm_to_dots(2, dpi)
    barcode_h = mm_to_dots(10, dpi)
    human = mm_to_dots(4, dpi) if barcode else 0
    band = barcode_h + human + gap if barcode else 0
    text_bottom = label_h - bottom - band
    if text_bottom < top + FONT_HEIGHT:
        text_bottom = label_h - bottom
        band = 0

    usable = max(1, label_w - left - right)
    scale = 2
    step = FONT_HEIGHT * scale + 4
    max_chars = max(1, usable // (FONT_WIDTH * scale))
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap(line, max_chars))
    if not wrapped:
        wrapped = [""]
    if top + len(wrapped) * step > text_bottom:
        scale = 1
        step = FONT_HEIGHT + 4
        max_chars = max(1, usable // FONT_WIDTH)
        wrapped = []
        for line in lines:
            wrapped.extend(_wrap(line, max_chars))
        if not wrapped:
            wrapped = [""]

    commands = ["CLS\r\n", "CODEPAGE 1251\r\n"]
    y = top
    for line in wrapped:
        if y + FONT_HEIGHT * scale > text_bottom:
            break
        commands.append(f'TEXT {left},{y},"{FONT}",0,{scale},{scale},"{line}"\r\n')
        y += step
    if band and barcode:
        # Тот же левый край, что у текста, и сразу под последней строкой.
        # wide=4: у Code 128 широкая полоса вдвое шире узкой, иначе штрихкод съезжает.
        barcode_y = min(y + gap, label_h - bottom - barcode_h - human)
        commands.append(f'BARCODE {left},{barcode_y},"128",{barcode_h},1,0,2,4,"{_clean(barcode)}"\r\n')
    copies = max(1, int(copies))
    commands.append(f"PRINT 1,{copies}\r\n")
    return "".join(commands)


def _job(printer, kind: str, labels: list[tuple[list[str], str]], copies: int = 1) -> bytes:
    width_mm, height_mm, _gap_mm = LABEL_MM[kind]
    dpi = int(printer.dpi or 203)
    label_w = mm_to_dots(width_mm, dpi)
    label_h = mm_to_dots(height_mm, dpi)
    gap_mm = int(getattr(printer, "gap_mm", 0) or 0)
    if gap_mm <= 0:
        gap_mm = 2
    parts = [
        f"GAP {gap_mm} mm,0 mm\r\n",
        "OFFSET 0 mm\r\n",
        "REFERENCE 0,0\r\n",
        "DIRECTION 1\r\n",
    ]
    for lines, barcode in labels:
        parts.append(_label(lines, barcode, label_w, label_h, dpi, copies))
    return "".join(parts).encode("cp1251", errors="replace")


def unit_label(printer, unit) -> bytes:
    return _job(printer, "unit", [(unit_lines(unit), unit.barcode)])


def _supplier_line(groups) -> str:
    names = []
    for group in groups:
        for unit in group.units:
            name = (unit.lot.supplier.name or "").strip()
            if name and name not in names:
                names.append(name)
    return ", ".join(names)


def _party_lines(pallet) -> list[str]:
    lines = []
    if pallet.order_id:
        number = (pallet.order.number or "").strip()
        if number:
            lines.append(f"Заказ {number}")
    if pallet.reserved_consignee_id:
        client = (pallet.reserved_consignee.name or "").strip()
    elif pallet.order_id and pallet.order.consignee_id:
        client = (pallet.order.consignee.name or "").strip()
    else:
        client = ""
    if client:
        lines.append(client)
    return lines


def pallet_labels(printer, pallet, copies: int = 2) -> bytes:
    groups = group_units(pallet.units.all())
    lines = [pallet.number, *_party_lines(pallet)]
    supplier = _supplier_line(groups)
    if supplier:
        lines.append(supplier)
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
    return _job(printer, "pallet", [(chunk, pallet.barcode) for chunk in chunks], copies)


def ship_sticker(printer, paper, groups) -> bytes:
    total = sum((group.kg for group in groups), start=Decimal("0"))
    lines = [
        paper.seller_name,
        paper.client_name,
        f"Задание {paper.number}",
        f"Нетто {format_kg(total)} кг",
    ]
    for group in groups[:6]:
        lines.append(f"{group.product} {group.caliber} {format_kg(group.kg)}кг")
    return _job(printer, "shipment", [(lines, paper.barcode)])


def test_label(printer) -> bytes:
    return _job(printer, "test", [(["WMS OK", "Проверка"], "")])
