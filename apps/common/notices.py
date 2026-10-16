from apps.common.models import Notice, NoticeCursor


def notify(user, lines: list[str]) -> None:
    if not lines:
        return
    actor = (getattr(user, "name", "") or getattr(user, "login", "") or "").strip() or "Пользователь"
    Notice.objects.bulk_create(
        [Notice(user=user if getattr(user, "pk", None) else None, actor=actor, text=f"{actor} — {line}") for line in lines]
    )


def notice_state(user) -> tuple[list[Notice], int]:
    if not getattr(user, "is_authenticated", False):
        return [], 0
    seen = NoticeCursor.objects.filter(user=user).values_list("seen_id", flat=True).first() or 0
    latest = list(Notice.objects.order_by("-id")[:80])
    for item in latest:
        item.unread = item.id > seen
    unread = Notice.objects.filter(id__gt=seen).count()
    return latest, unread


def mark_notices_seen(user) -> None:
    latest = Notice.objects.order_by("-id").values_list("id", flat=True).first() or 0
    NoticeCursor.objects.update_or_create(user=user, defaults={"seen_id": latest})
