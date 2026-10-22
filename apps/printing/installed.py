import os
import re
import subprocess
import time

from django.conf import settings

from apps.common.exceptions import PrintError

_CACHE = {"at": 0.0, "rows": None}
_HOST = re.compile(r"^[A-Za-z0-9.\-]+$")


def server_host() -> str:
    host = (settings.DATABASES["default"].get("HOST") or "").strip()
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return ""
    if not _HOST.match(host):
        raise PrintError("Некорректный адрес сервера принтеров")
    return host


def list_installed() -> list[dict]:
    now = time.monotonic()
    if _CACHE["rows"] is not None and now - _CACHE["at"] < 30:
        return _CACHE["rows"]
    rows = _list_windows(server_host()) if os.name == "nt" else _list_cups()
    _CACHE["at"] = now
    _CACHE["rows"] = rows
    return rows


def print_target(queue: str) -> str:
    host = server_host()
    if os.name == "nt" and host:
        return f"\\\\{host}\\{queue}"
    return queue


def _list_windows(host: str) -> list[dict]:
    computer = f" -ComputerName '{host}'" if host else ""
    script = (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
        f"Get-Printer{computer} | Where-Object {{ $_.DeviceType -eq 'Print' }} | "
        "ForEach-Object { $_.Name }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError("Не удалось получить принтеры сервера") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise PrintError(detail or "Сервер не отдал список принтеров")
    names = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return [{"name": name} for name in names]


def _list_cups() -> list[dict]:
    try:
        result = subprocess.run(["lpstat", "-p"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError("Не удалось получить принтеры сервера") from exc
    if result.returncode != 0:
        raise PrintError("На сервере не установлен список принтеров")
    names = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "printer" and parts[1] not in names:
            names.append(parts[1])
    return [{"name": name} for name in names]
