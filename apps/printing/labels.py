from decimal import Decimal

import qrcode

from apps.common.present import format_kg, unit_lines

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


def _qr_layout(payload: str, max_dots: int) -> tuple[int, int]:
    image = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    image.add_data(payload)
    image.make(fit=True)
    modules = max(1, int(image.modules_count))
    # Точка 3 на 203 dpi — 0,4 мм. Это в 1,5 раза крупнее прежней точки 2 и читается телефоном.
    cell = 3 if modules * 3 <= max_dots else max(1, max_dots // modules)
    return cell, modules * cell


def _qr_command(x: int, y: int, payload: str, cell: int) -> bytes:
    # В кавычках только LF: CR оборвал бы команду TSPL. UTF-8 читается телефоном так же, как QR на PDF.
    data = payload.replace("\r\n", "\n").replace("\r", "\n").replace('"', "'")
    prefix = f'QRCODE {x},{y},M,{cell},A,0,"'.encode("ascii")
    return prefix + data.encode("utf-8") + b'"\r\n'


def _wrap_lines(lines: list[str], usable: int, scale: int) -> tuple[int, list[str]]:
    step = FONT_HEIGHT * scale + 4
    max_chars = max(1, usable // (FONT_WIDTH * scale))
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap(line, max_chars))
    return step, wrapped or [""]


def _wrap_corner(lines: list[str], usable: int, scale: int, qr_side: int, quiet: int, top: int, text_bottom: int):
    """Строки рядом с QR уже, ниже кода — на всю ширину."""
    step = FONT_HEIGHT * scale + 4
    narrow = max(1, (usable - qr_side - quiet) // (FONT_WIDTH * scale))
    wide = max(1, usable // (FONT_WIDTH * scale))
    wrapped: list[str] = []
    y = top
    qr_span = top + qr_side + quiet
    for line in lines:
        parts = _wrap(line, narrow if y < qr_span else wide) or [""]
        for part in parts:
            wrapped.append(part)
            y += step
    if not _lines_fit(len(wrapped), top, step, scale, text_bottom):
        return step, None
    return step, wrapped


def _lines_fit(count: int, top: int, step: int, scale: int, text_bottom: int) -> bool:
    if count <= 0:
        return True
    return top + (count - 1) * step + FONT_HEIGHT * scale <= text_bottom


def _label(
    lines: list[str],
    barcode: str,
    label_w: int,
    label_h: int,
    dpi: int,
    copies: int = 1,
    *,
    symbol: str = "barcode",
) -> bytes:
    # 5 мм слева: печатающая головка TSC не начинает с самого края наклейки.
    left = mm_to_dots(5, dpi)
    top = mm_to_dots(3, dpi)
    right = mm_to_dots(4, dpi)
    bottom = mm_to_dots(3, dpi)
    gap = mm_to_dots(2, dpi)
    usable = max(1, label_w - left - right)
    qr_cell = 0
    qr_side = 0
    barcode_h = mm_to_dots(16, dpi)
    human = mm_to_dots(4, dpi) if barcode else 0
    if symbol == "qr" and barcode:
        # Сторона около 15 мм, справа от номера в верхнем углу.
        limit = min(mm_to_dots(22, dpi), usable)
        qr_cell, qr_side = _qr_layout(barcode, limit)
        quiet = max(gap, qr_cell * 4)
        band = qr_side
        text_bottom = label_h - bottom
        scale = 2
        step, wrapped = _wrap_corner(lines, usable, scale, qr_side, quiet, top, text_bottom)
        if wrapped is None:
            scale = 1
            step, wrapped = _wrap_corner(lines, usable, scale, qr_side, quiet, top, text_bottom)
        if wrapped is None:
            step, wrapped = _wrap_lines(lines, max(1, usable - qr_side - quiet), 1)
        gap = quiet
    else:
        band = barcode_h + human + gap if barcode else 0
        text_bottom = label_h - bottom - band
        if text_bottom < top + FONT_HEIGHT:
            text_bottom = label_h - bottom
            band = 0
        scale = 2
        step, wrapped = _wrap_lines(lines, usable, scale)
        if not _lines_fit(len(wrapped), top, step, scale, text_bottom):
            scale = 1
            step, wrapped = _wrap_lines(lines, usable, scale)

    commands = ["CLS\r\n", "CODEPAGE 1251\r\n"]
    y = top
    for line in wrapped:
        if symbol != "qr" and y + FONT_HEIGHT * scale > text_bottom:
            break
        commands.append(f'TEXT {left},{y},"{FONT}",0,{scale},{scale},"{line}"\r\n')
        y += step
    body = "".join(commands).encode("cp1251", errors="replace")
    if band and barcode and symbol == "qr" and qr_side:
        qr_x = label_w - right - qr_side
        qr_y = top
        body += _qr_command(qr_x, qr_y, barcode, qr_cell)
    elif band and barcode:
        # Тот же левый край, что у текста, и сразу под последней строкой.
        # wide=4: у Code 128 широкая полоса вдвое шире узкой, иначе штрихкод съезжает.
        barcode_y = min(y + gap, label_h - bottom - barcode_h - human)
        body += f'BARCODE {left},{barcode_y},"128",{barcode_h},1,0,2,4,"{_clean(barcode)}"\r\n'.encode(
            "cp1251", errors="replace"
        )
    copies = max(1, int(copies))
    body += f"PRINT 1,{copies}\r\n".encode("ascii")
    return body


def _job(printer, kind: str, labels: list[tuple[list[str], str]], copies: int = 1, *, symbol: str = "barcode") -> bytes:
    width_mm, height_mm, _gap_mm = LABEL_MM[kind]
    dpi = int(printer.dpi or 203)
    label_w = mm_to_dots(width_mm, dpi)
    label_h = mm_to_dots(height_mm, dpi)
    gap_mm = int(getattr(printer, "gap_mm", 0) or 0)
    if gap_mm <= 0:
        gap_mm = 2
    parts = [
        f"GAP {gap_mm} mm,0 mm\r\n".encode("ascii"),
        b"OFFSET 0 mm\r\n",
        b"REFERENCE 0,0\r\n",
        b"DIRECTION 1\r\n",
    ]
    for lines, barcode in labels:
        parts.append(_label(lines, barcode, label_w, label_h, dpi, copies, symbol=symbol))
    return b"".join(parts)


def unit_label(printer, unit) -> bytes:
    return _job(printer, "unit", [(unit_lines(unit), unit.barcode)])


def pallet_labels(printer, pallet, copies: int = 2) -> bytes:
    from apps.printing.passport import _qr_text, passport_lines

    units = list(
        pallet.units.exclude(status="rejected").select_related(
            "lot__product", "lot__caliber", "lot__supplier", "grade", "packaging"
        )
    )
    lines = passport_lines(pallet, units)
    return _job(printer, "pallet", [(lines, _qr_text(pallet, units))], copies, symbol="qr")


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
