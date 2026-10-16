import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

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
