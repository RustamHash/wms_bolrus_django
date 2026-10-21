from decimal import Decimal

from django import template

from apps.common.constants import PALLET_TARE, PALLET_TYPE
from apps.common.kg import as_kg, format_kg
from apps.common.present import describe_line
from apps.planning.services import remaining_kg

register = template.Library()


@register.filter
def kg(value):
    return format_kg(value or 0)


@register.filter
def startswith(value, prefix):
    return str(value or "").startswith(prefix)


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return 0
    if key in mapping:
        return mapping[key]
    text = str(key)
    if text in mapping:
        return mapping[text]
    try:
        number = int(key)
    except (TypeError, ValueError):
        return 0
    return mapping.get(number, 0)


@register.filter
def screen(user, key):
    from apps.accounts.access import screen_allows

    return screen_allows(user, key, "view")


@register.filter
def label(mapping, key):
    if isinstance(mapping, dict) and key in mapping:
        return mapping[key]
    return key


@register.filter
def goods_kg(lines):
    from apps.planning.services import sum_kg

    return sum_kg(lines)


@register.filter
def remaining(line):
    if not line:
        return 0
    return remaining_kg(line)


@register.filter
def describe(line):
    if not line:
        return ""
    return describe_line(line)


@register.filter
def allocation(unit):
    from apps.shipping.models import Allocation

    if not getattr(unit, "pk", None):
        return None
    return Allocation.objects.filter(unit_id=unit.pk).first()


@register.filter
def lookup(mapping, key):
    if not isinstance(mapping, dict):
        return None
    if key in mapping:
        return mapping[key]
    try:
        return mapping.get(int(key))
    except (TypeError, ValueError):
        return None


@register.filter
def is_in(value, container):
    return value in container


@register.filter
def sum_attr(items, attr):
    seq = items.all() if hasattr(items, "all") else items
    total = Decimal("0.000")
    for item in seq:
        total += as_kg(getattr(item, attr))
    return total


@register.filter
def join_attr(items, attr):
    seq = items.all() if hasattr(items, "all") else items
    return ", ".join(str(getattr(item, attr)) for item in seq)


@register.filter
def first_item(items):
    seq = items.all() if hasattr(items, "all") else items
    for item in seq:
        return item
    return None


@register.simple_tag
def pallet_tare(key):
    return PALLET_TARE.get(key, "0")


@register.simple_tag
def pallet_label(key):
    name = PALLET_TYPE.get(key, "")
    tare = format_kg(PALLET_TARE.get(key, "0"))
    if not name:
        return ""
    return f"{name} ({tare} кг)"
