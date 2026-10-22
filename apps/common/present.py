from dataclasses import dataclass, field
from decimal import Decimal

from apps.common.kg import as_kg, format_kg


def line_caliber(line) -> str:
    if line.caliber_mode == "exact" and line.caliber_id:
        return line.caliber.name
    if line.caliber_mode == "range" and line.caliber_from_id and line.caliber_to_id:
        return f"{line.caliber_from.name}–{line.caliber_to.name}"
    return ""


def describe_line(line) -> str:
    parts = [line.product_title()]
    caliber = line_caliber(line)
    if caliber:
        parts.append(caliber)
    grades = sorted(line.grades.all(), key=lambda item: (item.sort_order, item.name))
    if grades:
        parts.append("сортность: " + ", ".join(item.name for item in grades))
    return ", ".join(parts)


def unit_lines(unit) -> list[str]:
    lot = unit.lot
    return [
        lot.product.name,
        f"{lot.caliber.name} · {unit.grade.name}",
        f"{format_kg(unit.net_kg)} кг · {unit.packaging.name}",
        f"{lot.supplier.name} · {lot.received_on.strftime('%d.%m.%Y')}",
    ]


@dataclass
class UnitGroup:
    product: str
    caliber: str
    grade: str
    count: int = 0
    kg: Decimal = field(default_factory=lambda: Decimal("0.000"))
    units: list = field(default_factory=list)


def group_units(units) -> list[UnitGroup]:
    order: list[tuple] = []
    buckets: dict[tuple, UnitGroup] = {}
    for unit in units:
        if unit.status in ("rejected", "transformed"):
            continue
        key = (unit.lot.product.name, unit.lot.caliber.name, unit.grade.name)
        if key not in buckets:
            buckets[key] = UnitGroup(*key)
            order.append(key)
        group = buckets[key]
        group.units.append(unit)
        group.count += 1
        group.kg += as_kg(unit.net_kg)
    return [buckets[key] for key in order]
