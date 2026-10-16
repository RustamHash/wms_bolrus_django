import os
import socket
import subprocess
import tempfile
from pathlib import Path

from apps.common.exceptions import PrintError
from apps.common.present import group_units
from apps.printing import labels, pdfs
from apps.printing.models import Printer


def apply_printer(item: Printer, form) -> None:
    from apps.common.forms import optional_int, text

    name = text(form, "name")
    if not name:
        raise PrintError("Укажите название")
    printer_type = text(form, "printer_type")
    if printer_type not in ("tsc", "network"):
        raise PrintError("Выберите тип принтера")
    item.name = name
    item.printer_type = printer_type
    item.host = text(form, "host")
    item.port = optional_int(form, "port") or 9100
    item.queue_name = text(form, "queue_name")
    item.label_width_mm = optional_int(form, "label_width_mm") or 58
    item.label_height_mm = optional_int(form, "label_height_mm") or 40
    item.gap_mm = optional_int(form, "gap_mm") or 2
    item.dpi = optional_int(form, "dpi") or 203
    item.density = optional_int(form, "density") or 8
    item.is_default = form.get("is_default") == "1"
    item.save()
    if item.is_default:
        Printer.objects.filter(printer_type=printer_type).exclude(pk=item.pk).update(is_default=False)


def default_printer(printer_type: str) -> Printer:
    printer = Printer.objects.filter(printer_type=printer_type, is_default=True).first()
    if printer is None:
        printer = Printer.objects.filter(printer_type=printer_type).first()
    if printer is None:
        if printer_type == "tsc":
            raise PrintError("Не задан термопринтер TSC")
        raise PrintError("Не задан сетевой принтер")
    return printer


def send_raw(host: str, port: int, payload: bytes) -> None:
    if not host:
        raise PrintError("У принтера не задан адрес")
    try:
        with socket.create_connection((host, int(port or 9100)), timeout=5) as sock:
            sock.sendall(payload)
    except OSError as exc:
        raise PrintError(f"Принтер недоступен: {exc}") from exc


def send_tspl(printer: Printer, payload: bytes) -> str:
    send_raw(printer.host, printer.port or 9100, payload)
    return f"Этикетка отправлена на TSC «{printer.name}»"


def send_pdf(printer: Printer, payload: bytes, job_name: str) -> str:
    if printer.queue_name:
        return _print_windows_queue(printer.queue_name, payload, job_name)
    if printer.host:
        send_raw(printer.host, printer.port or 9100, payload)
        return f"PDF отправлен на {printer.host}:{printer.port or 9100}"
    raise PrintError("У сетевого принтера не заданы очередь Windows или адрес")


def _print_windows_queue(queue: str, payload: bytes, job_name: str) -> str:
    folder = Path(tempfile.gettempdir()) / "wms-print"
    folder.mkdir(exist_ok=True)
    path = folder / f"{job_name}.pdf"
    path.write_bytes(payload)
    script = Path(__file__).with_name("print_queue.ps1")
    env = os.environ.copy()
    env["WMS_PDF"] = str(path)
    env["WMS_PRINTER"] = queue
    env["WMS_JOB"] = job_name
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            env=env,
            capture_output=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrintError("Печать Windows не завершилась. Проверьте, что принтер не ждёт подтверждения") from exc
    except OSError as exc:
        raise PrintError(f"Не удалось вызвать печать Windows: {exc}") from exc
    if result.returncode != 0:
        detail = _ps_text(result.stderr) or _ps_text(result.stdout)
        raise PrintError(_print_failure(queue, detail))
    return f"PDF отправлен в очередь «{queue}»"


def _ps_text(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "cp866", "cp1251"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").strip()


def _print_failure(queue: str, detail: str) -> str:
    if detail in ("PRINTER_NOT_FOUND", "PRINTER_MISSING"):
        return f"Принтер «{queue}» не найден среди принтеров Windows"
    if detail == "PDF_MISSING":
        return "Не удалось подготовить PDF для печати"
    if detail == "PDF_EMPTY":
        return "PDF не содержит страниц"
    if detail:
        return detail
    return "Windows не приняла PDF"


def print_unit(unit, target: str) -> str:
    if target == "tsc":
        printer = default_printer("tsc")
        return send_tspl(printer, labels.unit_label(printer, unit))
    if target == "network":
        printer = default_printer("network")
        return send_pdf(printer, pdfs.unit_label_pdf(unit), f"unit-{unit.id}")
    raise PrintError("Неизвестная печать")


def print_pallet(pallet, target: str) -> str:
    if target == "tsc":
        printer = default_printer("tsc")
        return send_tspl(printer, labels.pallet_labels(printer, pallet))
    if target == "network":
        printer = default_printer("network")
        return send_pdf(printer, pdfs.pallet_passport_pdf(pallet), f"pallet-{pallet.id}")
    raise PrintError("Неизвестная печать")


def print_shipment(shipment, target: str, document: str) -> str:
    units = list(shipment.units.all())
    if document == "sticker" and target == "tsc":
        printer = default_printer("tsc")
        return send_tspl(printer, labels.shipment_sticker(printer, shipment, group_units(units)))
    if document == "sticker" and target == "network":
        printer = default_printer("network")
        return send_pdf(printer, pdfs.sticker_pdf(shipment, units), f"sticker-{shipment.id}")
    if document == "waybill":
        if target == "tsc":
            raise PrintError("Отгрузочный лист печатается PDF на сетевой принтер")
        printer = default_printer("network")
        return send_pdf(printer, pdfs.waybill_pdf(shipment, units), f"waybill-{shipment.id}")
    raise PrintError("Неизвестный документ")
