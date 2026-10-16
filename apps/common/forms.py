from datetime import date
from decimal import Decimal, InvalidOperation

from apps.common.exceptions import WarehouseError


def text(form, name: str) -> str:
    return (form.get(name) or "").strip()


def require_int(form, name: str, label: str) -> int:
    raw = text(form, name)
    if not raw:
        raise WarehouseError(f"Заполните поле «{label}»")
    try:
        return int(raw)
    except ValueError as exc:
        raise WarehouseError(f"Некорректное поле «{label}»") from exc


def optional_int(form, name: str) -> int | None:
    raw = text(form, name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise WarehouseError("Некорректное число") from exc


def optional_int_query(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_kg(raw: str, label: str = "Вес") -> Decimal:
    value_text = (raw or "").strip().replace(",", ".")
    if not value_text:
        raise WarehouseError(f"Заполните поле «{label}»")
    try:
        value = Decimal(value_text)
    except InvalidOperation as exc:
        raise WarehouseError("Некорректный вес") from exc
    value = value.quantize(Decimal("0.001"))
    if value <= 0:
        raise WarehouseError("Вес должен быть больше нуля")
    return value


def parse_date(raw: str, label: str) -> date:
    value = (raw or "").strip()
    if not value:
        raise WarehouseError(f"Заполните поле «{label}»")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WarehouseError(f"Некорректная дата в поле «{label}»") from exc


def query_date(raw: str | None) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def get_row(model, ident: int, label: str):
    obj = model.objects.filter(pk=ident).first()
    if obj is None:
        raise WarehouseError(f"Не найдено: {label}")
    return obj
