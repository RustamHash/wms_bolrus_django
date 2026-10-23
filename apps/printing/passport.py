import os
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import qrcode
from django.utils import timezone
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm

from apps.common.exceptions import PrintError
from apps.common.kg import as_kg, format_kg

TEMPLATE = Path(__file__).resolve().with_name("docx") / "pallet_passport.docx"
_word_lock = threading.Lock()


def render_pallet_passport_pdf(pallet) -> bytes:
    if not TEMPLATE.exists():
        raise PrintError("Нет шаблона паспорта паллеты")
    units = list(
        pallet.units.exclude(status="rejected").select_related(
            "lot__product", "lot__caliber", "lot__supplier", "grade", "packaging"
        )
    )
    folder = Path(tempfile.mkdtemp(prefix="wms-passport-"))
    png = folder / "qr.png"
    docx_path = folder / "passport.docx"
    pdf_path = folder / "passport.pdf"
    try:
        _write_qr(png, _qr_text(pallet, units))
        _fill_docx(docx_path, png, _context(pallet, units))
        _docx_to_pdf(docx_path, pdf_path)
        return pdf_path.read_bytes()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


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
    }


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


def _write_qr(path: Path, payload: str) -> None:
    image = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    image.add_data(payload)
    image.make(fit=True)
    image.make_image(fill_color="black", back_color="white").save(path)


def _fill_docx(docx_path: Path, png: Path, context: dict) -> None:
    document = DocxTemplate(TEMPLATE)
    context = {
        **context,
        "qr_top": InlineImage(document, str(png), width=Mm(32)),
        "qr_bottom": InlineImage(document, str(png), width=Mm(32)),
    }
    document.render(context)
    document.save(docx_path)


def _docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    if os.name == "nt":
        _docx_to_pdf_word(docx_path, pdf_path)
        return
    _docx_to_pdf_office(docx_path, pdf_path)


def _docx_to_pdf_word(docx_path: Path, pdf_path: Path) -> None:
    script = Path(__file__).with_name("word_pdf.ps1")
    env = os.environ.copy()
    env["WMS_DOCX"] = str(docx_path)
    env["WMS_PDF_OUT"] = str(pdf_path)
    with _word_lock:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                env=env,
                capture_output=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            raise PrintError("Word не собрал паспорт. Закройте зависший Word и повторите") from exc
        except OSError as exc:
            raise PrintError(f"Не удалось вызвать Word: {exc}") from exc
    if result.returncode != 0 or not pdf_path.exists():
        detail = _ps_text(result.stderr) or _ps_text(result.stdout) or "Word не собрал PDF"
        raise PrintError(detail)


def _office_bin() -> str:
    for name in ("libreoffice", "soffice"):
        found = shutil.which(name)
        if found:
            return found
    raise PrintError(
        "На сервере не установлен LibreOffice. "
        "От root: bash /opt/wms/deploy/install-print.sh"
    )


def _docx_to_pdf_office(docx_path: Path, pdf_path: Path) -> None:
    binary = _office_bin()
    outdir = docx_path.parent
    profile = (outdir / "lo-profile").resolve().as_uri()
    env = os.environ.copy()
    env["HOME"] = str(outdir)
    try:
        result = subprocess.run(
            [
                binary,
                f"-env:UserInstallation={profile}",
                "--headless",
                "--norestore",
                "--nologo",
                "--nolockcheck",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(outdir),
                str(docx_path),
            ],
            env=env,
            capture_output=True,
            timeout=45,
            cwd=str(outdir),
        )
    except subprocess.TimeoutExpired as exc:
        raise PrintError("LibreOffice не собрал паспорт за отведённое время") from exc
    except OSError as exc:
        raise PrintError(f"Не удалось вызвать LibreOffice: {exc}") from exc
    produced = pdf_path if pdf_path.exists() else next(outdir.glob("*.pdf"), None)
    if produced is not None and produced != pdf_path:
        produced.replace(pdf_path)
    if result.returncode != 0 or not pdf_path.exists():
        detail = _ps_text(result.stderr) or _ps_text(result.stdout) or "LibreOffice не собрал PDF"
        raise PrintError(detail)


def _ps_text(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "cp866", "cp1251"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").strip()
