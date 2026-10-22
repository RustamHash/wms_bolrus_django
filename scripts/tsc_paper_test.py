"""Одна и та же команда TSC без SIZE, GAP и DENSITY.

На этом компьютере принтер сидит на USB, очередь Windows «TSC TE200».
Скрипт кладёт байты в очередь как RAW, драйвер их не перерисовывает.

Запуск на USB:
    python scripts/tsc_paper_test.py

Другая очередь:
    python scripts/tsc_paper_test.py --queue "TSC TE200"

Сеть, порт 9100:
    python scripts/tsc_paper_test.py 192.168.1.50
"""

import ctypes
import socket
import sys
from ctypes import wintypes

PAYLOAD = (
    "CLS\r\n"
    'TEXT 20,20,"3",0,1,1,"NO SIZE"\r\n'
    'TEXT 20,70,"3",0,1,1,"SAME JOB"\r\n'
    "BAR 20,130,280,8\r\n"
    "PRINT 1\r\n"
)

DEFAULT_QUEUE = "TSC TE200"


class DOCINFO(ctypes.Structure):
    _fields_ = [
        ("pDocName", wintypes.LPWSTR),
        ("pOutputFile", wintypes.LPWSTR),
        ("pDatatype", wintypes.LPWSTR),
    ]


def send_queue(queue: str, payload: bytes) -> None:
    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    winspool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
    winspool.OpenPrinterW.restype = wintypes.BOOL
    winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
    winspool.ClosePrinter.restype = wintypes.BOOL
    winspool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(DOCINFO)]
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
        raise OSError(ctypes.get_last_error(), f"очередь «{queue}» не открылась")
    try:
        info = DOCINFO("tsc-paper-test", None, "RAW")
        if not winspool.StartDocPrinterW(handle, 1, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "Windows не приняла задание")
        try:
            if not winspool.StartPagePrinter(handle):
                raise OSError(ctypes.get_last_error(), "Windows не открыла страницу")
            written = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(payload)
            if not winspool.WritePrinter(handle, buffer, len(payload), ctypes.byref(written)):
                raise OSError(ctypes.get_last_error(), "Windows не записала данные")
            if written.value != len(payload):
                raise OSError(0, f"ушло {written.value} байт из {len(payload)}")
            winspool.EndPagePrinter(handle)
        finally:
            winspool.EndDocPrinter(handle)
    finally:
        winspool.ClosePrinter(handle)


def send_tcp(host: str, port: int, payload: bytes) -> None:
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.sendall(payload)


def main() -> None:
    args = sys.argv[1:]
    data = PAYLOAD.encode("ascii")
    print(PAYLOAD, end="")
    if not args:
        send_queue(DEFAULT_QUEUE, data)
        print(f"Отправлено {len(data)} байт в очередь «{DEFAULT_QUEUE}»")
        return
    if args[0] == "--queue":
        if len(args) < 2:
            print("Нужно имя очереди: python scripts/tsc_paper_test.py --queue \"TSC TE200\"")
            sys.exit(1)
        send_queue(args[1], data)
        print(f"Отправлено {len(data)} байт в очередь «{args[1]}»")
        return
    host = args[0]
    port = int(args[1]) if len(args) > 1 else 9100
    send_tcp(host, port, data)
    print(f"Отправлено {len(data)} байт на {host}:{port}")


if __name__ == "__main__":
    main()
