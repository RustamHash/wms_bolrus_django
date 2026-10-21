import sys
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.onec.exceptions import OnecError
from apps.onec.sync import delivery_period, sync_customer_orders, sync_purchase_orders


class Command(BaseCommand):
    help = "Загрузка заказов из 1С в WMS по дате поставки/отгрузки (только чтение OData)"

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=("purchase", "customer", "both"), default="both")
        parser.add_argument("--top", type=int, default=500)
        parser.add_argument("--date-from", default="", help="YYYY-MM-DD; по умолчанию завтра")
        parser.add_argument("--date-to", default="", help="YYYY-MM-DD; по умолчанию завтра")
        parser.add_argument("--tomorrow", action="store_true", help="Явно взять завтра (игнор date-from/to)")
        parser.add_argument("--no-update", action="store_true", help="Не обновлять уже загруженные")
        parser.add_argument("--user", default="", help="Логин пользователя WMS для created_by")

    def handle(self, *args, **options):
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        user = self._user(options["user"])
        update = not options["no_update"]
        if options["tomorrow"]:
            date_from, date_to = delivery_period()
        else:
            date_from, date_to = delivery_period(
                _parse_date(options["date_from"]),
                _parse_date(options["date_to"]),
            )
        kind = options["kind"]
        top = options["top"]
        self.stdout.write(f"Период: {date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}")
        try:
            if kind in ("purchase", "both"):
                report = sync_purchase_orders(
                    user, update=update, top=top, date_from=date_from, date_to=date_to
                )
                self._print("Заказы поставщикам (дата поступления)", report)
            if kind in ("customer", "both"):
                report = sync_customer_orders(
                    user, update=update, top=top, date_from=date_from, date_to=date_to
                )
                self._print("Заказы покупателей (дата отгрузки)", report)
        except OnecError as exc:
            raise CommandError(str(exc)) from exc

    def _user(self, username: str):
        User = get_user_model()
        if username:
            user = User.objects.filter(login=username).first()
            if user is None:
                raise CommandError(f"Пользователь WMS «{username}» не найден")
            return user
        user = User.objects.filter(role="admin").order_by("id").first()
        if user is None:
            user = User.objects.order_by("id").first()
        if user is None:
            raise CommandError("В WMS нет пользователей")
        return user

    def _print(self, title: str, report):
        self.stdout.write(self.style.MIGRATE_HEADING(title))
        self.stdout.write(
            f"  создано={len(report.created)} обновлено={len(report.updated)} "
            f"без изменений={len(report.unchanged)} пропущено статусов={report.other_status}"
        )
        if report.duplicates:
            self.stdout.write(f"  уже были (без update): {len(report.duplicates)}")
        if report.closed:
            self.stdout.write(f"  не обновлены (закрыты/приняты): {len(report.closed)}")
        if report.blocked:
            self.stdout.write(f"  блокировки: {len(report.blocked)}")
        if report.empty:
            self.stdout.write(f"  пустые: {', '.join(report.empty[:10])}")
        if report.missing_files:
            for item in report.missing_files[:10]:
                self.stdout.write(self.style.WARNING(f"  {item}"))
        if report.missing_codes:
            self.stdout.write(f"  нет кодов: {len(report.missing_codes)}")
        if report.skipped_units:
            self.stdout.write(f"  не кг: {len(report.skipped_units)}")


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()
