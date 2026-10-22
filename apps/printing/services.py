import ctypes
import os
import socket
import subprocess
import tempfile
from ctypes import wintypes
from pathlib import Path

from apps.common.exceptions import PrintError
from apps.common.present import group_units
from apps.printing import labels, pdfs
from apps.printing.models import Printer


def apply_printer(item: Printer, form) -> None:
    from apps.common.forms import text
    from apps.printing.installed import list_installed

    name = text(form, "name")
    if not name:
        raise PrintError("Укажите название")
    queue = text(form, "queue_name")
    installed = {row["name"] for row in list_installed()}
    if queue not in installed:
        raise PrintError("Выберите принтер, установленный на сервере")
    item.name = name
    item.printer_type = "tsc" if "tsc" in queue.lower() else "network"
    item.port = 9100
    item.queue_name = queue
    item.host = ""
    item.is_default = form.get("is_default") == "1"
    item.save()
    if item.is_default:
        Printer.objects.filter(printer_type=item.printer_type).exclude(pk=item.pk).update(is_default=False)


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
    if printer.queue_name:
        from apps.printing.installed import print_target

        target = print_target(printer.queue_name)
        _print_raw_queue(target, payload, f"tsc-{printer.id}")
        return f"Этикетка отправлена в очередь «{printer.queue_name}»"
    send_raw(printer.host, printer.port or 9100, payload)
    return f"Этикетка отправлена на TSC «{printer.name}»"


class _DocInfo(ctypes.Structure):
    _fields_ = [
        ("pDocName", wintypes.LPWSTR),
        ("pOutputFile", wintypes.LPWSTR),
        ("pDatatype", wintypes.LPWSTR),
    ]


def _print_raw_queue(queue: str, payload: bytes, job_name: str) -> None:
    if os.name == "nt":
        _print_raw_windows(queue, payload, job_name)
        return
    _print_raw_cups(queue, payload, job_name)


def _print_raw_windows(queue: str, payload: bytes, job_name: str) -> None:
    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    winspool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
    winspool.OpenPrinterW.restype = wintypes.BOOL
    winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
    winspool.ClosePrinter.restype = wintypes.BOOL
    winspool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_DocInfo)]
    winspool.StartDocPrinterW.restype = wintypes.DWORD
    winspool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    winspool.EndDocPrinter.restype = wintypes.BOOL
    winspool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.StartPagePrinter.restype = wintypes.BOOL
    winspool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.EndPagePrinter.restype = wintypes.BOOL
    winspool.WritePrinter.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    winspool.WritePrinter.restype = wintypes.BOOL

    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(queue, ctypes.byref(handle), None):
        raise PrintError(f"Принтер «{queue}» не найден среди принтеров Windows")
    try:
        info = _DocInfo(job_name, None, "RAW")
        if not winspool.StartDocPrinterW(handle, 1, ctypes.byref(info)):
            raise PrintError(_windows_print_error(queue))
        try:
            if not winspool.StartPagePrinter(handle):
                raise PrintError(_windows_print_error(queue))
            written = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(payload)
            if not winspool.WritePrinter(handle, buffer, len(payload), ctypes.byref(written)):
                raise PrintError(_windows_print_error(queue))
            if written.value != len(payload):
                raise PrintError(f"В очередь «{queue}» ушло {written.value} байт из {len(payload)}")
            if not winspool.EndPagePrinter(handle):
                raise PrintError(_windows_print_error(queue))
        finally:
            winspool.EndDocPrinter(handle)
    finally:
        winspool.ClosePrinter(handle)


def _windows_print_error(queue: str) -> str:
    error = ctypes.WinError(ctypes.get_last_error())
    detail = str(error).strip()
    if detail:
        return f"Очередь «{queue}» не приняла этикетку: {detail}"
    return f"Очередь «{queue}» не приняла этикетку"


def _print_raw_cups(queue: str, payload: bytes, job_name: str) -> None:
    try:
        result = subprocess.run(
            ["lp", "-d", queue, "-o", "raw", "-t", job_name],
            input=payload,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrintError(f"Очередь «{queue}» не приняла этикетку") from exc
    except OSError as exc:
        raise PrintError(f"Не удалось отправить этикетку в очередь «{queue}»") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        if detail:
            raise PrintError(detail)
        raise PrintError(f"Очередь «{queue}» не приняла этикетку")


def send_pdf(printer: Printer, payload: bytes, job_name: str) -> str:
    if printer.queue_name:
        from apps.printing.installed import print_target

        return _print_windows_queue(print_target(printer.queue_name), payload, job_name)
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
