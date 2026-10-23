from apps.common.exceptions import WarehouseError
from apps.common.forms import text
from apps.storage.models import WarehouseCell, WarehouseZone


def place_form_context(cell: WarehouseCell | None = None) -> dict:
    return {
        "warehouse_zones": list(WarehouseZone.objects.order_by("code")),
        "warehouse_cells": list(WarehouseCell.objects.select_related("zone").order_by("zone__code", "code")),
        "selected_zone_id": cell.zone_id if cell is not None else None,
        "selected_cell_id": cell.id if cell is not None else None,
    }


def resolve_place(form, *, required: bool) -> WarehouseCell | None:
    zone_scan = text(form, "zone_scan")
    cell_scan = text(form, "cell_scan")
    zone_raw = text(form, "zone_id")
    cell_raw = text(form, "cell_id")
    if not any((zone_scan, cell_scan, zone_raw, cell_raw)):
        if required:
            raise WarehouseError("Укажите ячейку")
        return None
    zone = _zone(zone_raw, zone_scan)
    cell = _cell(cell_raw, cell_scan, zone)
    if zone is None and cell is not None:
        zone = cell.zone
    if cell is None:
        raise WarehouseError("Укажите ячейку")
    if zone is not None and cell.zone_id != zone.id:
        raise WarehouseError("Ячейка не относится к этой зоне")
    return cell


def _int(raw: str, label: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise WarehouseError(f"Выберите {label}") from exc


def _zone(raw_id: str, scan: str) -> WarehouseZone | None:
    zone = None
    if raw_id:
        zone = WarehouseZone.objects.filter(pk=_int(raw_id, "зону")).first()
        if zone is None:
            raise WarehouseError("Такой зоны нет")
    if scan:
        found = (
            WarehouseZone.objects.filter(code__iexact=scan).first()
            or WarehouseZone.objects.filter(barcode__iexact=scan).first()
            or WarehouseZone.objects.filter(name__iexact=scan).first()
        )
        if found is None:
            raise WarehouseError("Такой зоны нет")
        if zone is not None and found.id != zone.id:
            raise WarehouseError("Код зоны не совпадает с выбранной зоной")
        zone = found
    return zone


def _cell(raw_id: str, scan: str, zone: WarehouseZone | None) -> WarehouseCell | None:
    cell = None
    if raw_id:
        cell = WarehouseCell.objects.select_related("zone").filter(pk=_int(raw_id, "ячейку")).first()
        if cell is None:
            raise WarehouseError("Такой ячейки нет")
    if scan:
        found = _match_cell(scan, zone)
        if cell is not None and found.id != cell.id:
            raise WarehouseError("Код ячейки не совпадает с выбранной ячейкой")
        cell = found
    return cell


def _match_cell(scan: str, zone: WarehouseZone | None) -> WarehouseCell:
    query = WarehouseCell.objects.select_related("zone")
    if zone is not None:
        query = query.filter(zone=zone)
    found = list(query.filter(barcode__iexact=scan)[:2])
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise WarehouseError("Такой QR есть у нескольких ячеек")
    needle = "".join(scan.split()).casefold()
    labeled = [cell for cell in query if "".join(cell.label().split()).casefold() == needle]
    if len(labeled) == 1:
        return labeled[0]
    if len(labeled) > 1:
        raise WarehouseError("Такое имя есть у нескольких ячеек")
    coded = list(query.filter(code__iexact=scan)[:2])
    if len(coded) == 1:
        return coded[0]
    if len(coded) > 1:
        raise WarehouseError("Такое место есть в нескольких зонах. Укажите ячейку целиком, например А-01")
    raise WarehouseError("Такой ячейки нет")
