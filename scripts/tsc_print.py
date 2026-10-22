"""Печать текста на TSC по сети, порт 9100.

Меняйте переменные ниже и запускайте:
    python scripts/tsc_print.py

Обязательны: HOST, WIDTH_MM, HEIGHT_MM, LINES.
Без SIZE принтер эту команду молча не печатает.
GAP_MM = 0 протягивает ровно HEIGHT_MM и не ищет зазор между наклейками.
"""

import socket

# Куда слать. Порт 9100 — сырой вход TSC, его менять не нужно.
HOST = "192.168.1.133"
PORT = 9100

# Размер этикетки в миллиметрах. Эти числа попадают в команду SIZE.
WIDTH_MM = 100
HEIGHT_MM = 70

# Зазор между наклейками, мм. 0 — не искать зазор, протянуть HEIGHT_MM.
GAP_MM = 2

# Что печатать. Каждая строка списка — отдельная строка на этикетке.
LINES = [
    "WMS OK",
    "Проверка",
]

# Кодовая страница кириллицы у TSC. Латиница печатается и без неё.
CODEPAGE = "1251"

# Встроенный шрифт «3», увеличение 2. Координаты в точках, не в миллиметрах.
FONT = "3"
SCALE = 2
X = 50
FIRST_Y = 50
LINE_STEP = 50


def build() -> bytes:
    if not LINES:
        raise SystemExit("LINES пустой: нечего печатать")
    parts = [
        f"SIZE {int(WIDTH_MM)} mm,{int(HEIGHT_MM)} mm\r\n",
        f"GAP {int(GAP_MM)} mm,0 mm\r\n",
        "DIRECTION 1\r\n",
        "CLS\r\n",
        f"CODEPAGE {CODEPAGE}\r\n",
    ]
    y = FIRST_Y
    for line in LINES:
        text = str(line).replace('"', "'")
        parts.append(f'TEXT {X},{y},"{FONT}",0,{SCALE},{SCALE},"{text}"\r\n')
        y += LINE_STEP
    parts.append("PRINT 1,1\r\n")
    return "".join(parts).encode("cp1251", errors="replace")


def send(payload: bytes) -> None:
    with socket.create_connection((HOST, PORT), timeout=5) as sock:
        sock.sendall(payload)


def main() -> None:
    payload = build()
    print(payload.decode("cp1251"))
    send(payload)
    print(f"Отправлено {len(payload)} байт на {HOST}:{PORT}")


if __name__ == "__main__":
    main()
