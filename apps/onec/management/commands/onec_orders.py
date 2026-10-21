import sys

from django.core.management.base import BaseCommand, CommandError

from apps.onec.exceptions import OnecError
from apps.onec.orders import OnecOrders


class Command(BaseCommand):
    help = "Проверка OData 1С: заказы поставщикам / покупателям"

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=("purchase", "customer", "both"),
            default="both",
            help="Тип заказов",
        )
        parser.add_argument("--top", type=int, default=5, help="Сколько документов взять")
        parser.add_argument("--number", default="", help="Номер документа в 1С")
        parser.add_argument("--no-lines", action="store_true", help="Без строк запасов")

    def handle(self, *args, **options):
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            api = OnecOrders()
        except OnecError as exc:
            raise CommandError(str(exc)) from exc

        kind = options["kind"]
        top = options["top"]
        number = (options["number"] or "").strip() or None
        with_lines = not options["no_lines"]

        try:
            if kind in ("purchase", "both"):
                if number:
                    rows = [api.find_purchase_by_number(number, with_lines=with_lines)]
                else:
                    rows = api.list_purchase_orders(top=top, with_lines=with_lines)
                self._print_block("Заказы поставщикам", rows)
            if kind in ("customer", "both"):
                if number:
                    rows = [api.find_customer_by_number(number, with_lines=with_lines)]
                else:
                    rows = api.list_customer_orders(top=top, with_lines=with_lines)
                self._print_block("Заказы покупателей", rows)
        except OnecError as exc:
            raise CommandError(str(exc)) from exc

    def _print_block(self, title: str, orders):
        self.stdout.write(self.style.MIGRATE_HEADING(title))
        orders = [item for item in orders if item is not None]
        if not orders:
            self.stdout.write("  (пусто)")
            return
        for order in orders:
            when = order.date.strftime("%d.%m.%Y %H:%M") if order.date else "—"
            self.stdout.write(
                f"  {order.number} | {when} | {order.party.name or '—'} | {order.state or '—'}"
            )
            if order.comment:
                self.stdout.write(f"    комментарий: {order.comment}")
            for line in order.lines:
                unit = line.unit or "—"
                code = line.product.code or "—"
                name = line.product.name or line.content or "—"
                self.stdout.write(f"    {line.line_number}. [{code}] {name} — {line.qty} {unit}")
