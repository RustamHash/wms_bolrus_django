from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, text
from apps.logistics.models import Driver, Vehicle


def _optional_positive(form, name: str, label: str) -> int | None:
    raw = text(form, name)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise WarehouseError(f"Некорректное поле «{label}»") from exc
    if value <= 0:
        raise WarehouseError(f"«{label}» должно быть больше нуля")
    return value


def apply_vehicle(item: Vehicle, form) -> Vehicle:
    plate = text(form, "plate_number")
    if not plate:
        raise WarehouseError("Заполните поле «Госномер»")
    item.plate_number = plate
    item.brand_model = text(form, "brand_model")
    item.trailer_plate = text(form, "trailer_plate")
    item.load_capacity_kg = _optional_positive(form, "load_capacity_kg", "Тоннаж, кг")
    item.pallet_capacity = _optional_positive(form, "pallet_capacity", "Кол-во паллет")
    item.is_active = text(form, "is_active") == "1"
    item.save()
    return item


def apply_driver(item: Driver, form) -> Driver:
    name = text(form, "full_name")
    if not name:
        raise WarehouseError("Заполните поле «ФИО»")
    item.full_name = name
    item.phone = text(form, "phone")
    vehicle_id = optional_int(form, "vehicle_id")
    if vehicle_id:
        vehicle = Vehicle.objects.filter(pk=vehicle_id, is_active=True).first()
        if vehicle is None:
            raise WarehouseError("Автомобиль не найден")
        item.vehicle = vehicle
    else:
        item.vehicle = None
    item.is_active = text(form, "is_active") == "1"
    item.save()
    return item
