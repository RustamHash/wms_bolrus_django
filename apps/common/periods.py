"""Пресеты периода как в журнале заказов: сегодня, неделя, месяц, своя дата."""
from datetime import date, timedelta

from django.utils import timezone

from apps.common.forms import query_date


def period_date(period_type: str, today: date | None = None) -> date | None:
    today = today or timezone.localdate()
    if period_type == "today":
        return today
    if period_type == "yesterday":
        return today - timedelta(days=1)
    if period_type == "tomorrow":
        return today + timedelta(days=1)
    if period_type == "start_of_week":
        return today - timedelta(days=today.weekday())
    if period_type == "start_of_last_week":
        return today - timedelta(days=today.weekday() + 7)
    if period_type == "end_of_week":
        return today + timedelta(days=6 - today.weekday())
    if period_type == "end_of_last_week":
        return today + timedelta(days=6 - today.weekday() - 7)
    if period_type == "start_of_month":
        return today.replace(day=1)
    if period_type == "start_of_last_month":
        first = today.replace(day=1)
        return (first - timedelta(days=1)).replace(day=1)
    if period_type == "end_of_month":
        if today.month == 12:
            return date(today.year, 12, 31)
        return date(today.year, today.month + 1, 1) - timedelta(days=1)
    if period_type == "end_of_last_month":
        return today.replace(day=1) - timedelta(days=1)
    return None


def _bound(kind: str, raw: str) -> date | None:
    if kind == "custom":
        return query_date(raw)
    if kind:
        return period_date(kind)
    return None


def range_from_query(params, prefix: str, *, default_from: str = "") -> dict:
    """Границы «от/до» по пресету. Пустой «от» при заданном «до» считается сегодняшним днём."""
    from_type = (params.get(f"{prefix}_from_type") or "").strip()
    to_type = (params.get(f"{prefix}_to_type") or "").strip()
    from_value = (params.get(f"{prefix}_from") or "").strip()
    to_value = (params.get(f"{prefix}_to") or "").strip()
    if not any((from_type, to_type, from_value, to_value)):
        if not default_from:
            return {
                "from_type": "",
                "from_value": "",
                "to_type": "",
                "to_value": "",
                "from_date": None,
                "to_date": None,
            }
        from_type = default_from
    from_date = _bound(from_type, from_value)
    to_date = _bound(to_type, to_value)
    if from_date is None and (to_type or to_value):
        from_date = period_date("today")
    return {
        "from_type": from_type,
        "from_value": from_value if from_type == "custom" else "",
        "to_type": to_type,
        "to_value": to_value if to_type == "custom" else "",
        "from_date": from_date,
        "to_date": to_date,
    }
