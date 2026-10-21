import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _column(ref: str) -> str:
    return "".join(char for char in ref if char.isalpha())


def _row_number(ref: str) -> int:
    digits = "".join(char for char in ref if char.isdigit())
    return int(digits or 0)


def read_rows(source: Path | bytes, sheet: int = 1) -> dict[int, dict[str, str]]:
    handle = source if isinstance(source, Path) else io.BytesIO(source)
    with zipfile.ZipFile(handle) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", NS):
                shared.append("".join(node.text or "" for node in item.findall(".//m:t", NS)))
        sheet_name = f"xl/worksheets/sheet{sheet}.xml"
        if sheet_name not in book.namelist():
            raise ValueError(f"В книге нет листа {sheet}")
        root = ET.fromstring(book.read(sheet_name))
    rows: dict[int, dict[str, str]] = {}
    for cell in root.findall(".//m:c", NS):
        ref = cell.get("r") or ""
        kind = cell.get("t")
        value = cell.find("m:v", NS)
        inline = cell.find("m:is", NS)
        text = ""
        if kind == "s" and value is not None and value.text:
            text = shared[int(value.text)]
        elif kind == "inlineStr" and inline is not None:
            text = "".join(node.text or "" for node in inline.findall(".//m:t", NS))
        elif value is not None and value.text:
            text = value.text
        rows.setdefault(_row_number(ref), {})[_column(ref)] = text
    return rows


_STYLES = {
    "text": 0,
    "header": 1,
    "num": 2,
    "date": 3,
    "group": 4,
    "group_num": 5,
    "total": 6,
    "total_num": 7,
}


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _excel_day(value: date) -> int:
    return (value - date(1899, 12, 30)).days


def _split_cell(cell):
    style = None
    if isinstance(cell, tuple):
        cell, style = cell
    if isinstance(cell, datetime):
        cell = cell.date()
    if style is None:
        if isinstance(cell, date):
            style = "date"
        elif isinstance(cell, (Decimal, int, float)) and not isinstance(cell, bool):
            style = "num"
        else:
            style = "text"
    return cell, _STYLES.get(style, 0)


def _cell_xml(ref: str, cell) -> str:
    value, style = _split_cell(cell)
    if value is None or value == "":
        if not style:
            return ""
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, date):
        return f'<c r="{ref}" s="{style}"><v>{_excel_day(value)}</v></c>'
    if isinstance(value, Decimal):
        return f'<c r="{ref}" s="{style}"><v>{format(value, "f")}</v></c>'
    if isinstance(value, bool):
        value = "1" if value else "0"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: list[list]) -> str:
    width = max((len(row) for row in rows), default=1)
    lengths = [10] * width
    body = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, cell in enumerate(row, start=1):
            value, _style = _split_cell(cell)
            shown = "" if value is None else str(value)
            lengths[col_index - 1] = min(48, max(lengths[col_index - 1], len(shown) + 2))
            xml = _cell_xml(f"{_col_name(col_index)}{row_index}", cell)
            if xml:
                cells.append(xml)
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(lengths, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<cols>{cols}</cols><sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def write_book(sheets: list[tuple[str, list[list]]]) -> bytes:
    """Книга xlsx. Ячейка — значение или пара (значение, стиль)."""
    buffer = io.BytesIO()
    overrides = [
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    workbook_sheets = []
    rels = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as book:
        for index, (name, rows) in enumerate(sheets, start=1):
            safe = (name or f"Лист{index}").translate(str.maketrans("", "", ":\\/?*[]"))[:31] or f"Лист{index}"
            overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
            workbook_sheets.append(f'<sheet name="{escape(safe)}" sheetId="{index}" r:id="rId{index}"/>')
            rels.append(
                f'<Relationship Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
            )
            book.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))
        style_id = len(sheets) + 1
        rels.append(
            f'<Relationship Id="rId{style_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        book.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            + "".join(overrides)
            + "</Types>",
        )
        book.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        book.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>',
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>",
        )
        book.writestr("xl/styles.xml", _styles_xml())
    return buffer.getvalue()


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="2">
    <numFmt numFmtId="164" formatCode="0.000"/>
    <numFmt numFmtId="165" formatCode="dd.mm.yyyy"/>
  </numFmts>
  <fonts count="2">
    <font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>
  </fonts>
  <fills count="5">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEEF2EE"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE7F0EA"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF7F8F6"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="0" fontId="1" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
    <xf numFmtId="164" fontId="1" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyNumberFormat="1"/>
    <xf numFmtId="0" fontId="1" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
    <xf numFmtId="164" fontId="1" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1" applyNumberFormat="1"/>
  </cellXfs>
</styleSheet>
"""
