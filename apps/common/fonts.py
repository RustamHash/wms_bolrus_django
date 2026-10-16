import os

from apps.common.exceptions import WarehouseError


def find_font() -> str:
    candidates = [
        os.environ.get("WMS_FONT"),
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise WarehouseError("Не найден шрифт с кириллицей. Задайте путь в переменной WMS_FONT")
