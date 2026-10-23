from django.core.management.base import BaseCommand

from apps.accounts.models import User, new_badge_token
from apps.catalog.models import Caliber, Grade, Packaging, Seller


class Command(BaseCommand):
    help = "Справочники и учётные записи пустой базы"

    def handle(self, *args, **options):
        if not User.objects.exists():
            rows = [
                ("Администратор", "admin", "admin", "0000", "admin"),
                ("Приёмка", "receiver", "receiver", "1111", "receiver"),
                ("Склад", "warehouse", "warehouse", "2222", "warehouse"),
            ]
            for name, login_name, password, code, role in rows:
                user = User(
                    name=name,
                    login=login_name,
                    tsd_code=code,
                    badge_token=new_badge_token(),
                    role=role,
                    is_active=True,
                )
                user.set_password(password)
                user.save()
        if not Seller.objects.exists():
            Seller.objects.bulk_create(
                [
                    Seller(code="1", name="Байчора", requisites="ИП «Байчора»"),
                    Seller(code="2", name="БолРус", requisites="ООО «Большая Русь»"),
                ]
            )
        if not Grade.objects.exists():
            Grade.objects.bulk_create(
                [
                    Grade(name="Экстра", sort_order=1),
                    Grade(name="1 Сорт", sort_order=2),
                    Grade(name="2 Сорт", sort_order=3),
                    Grade(name="Нестандарт", sort_order=4),
                    Grade(name="Производство", sort_order=5),
                ]
            )
        if not Caliber.objects.exists():
            Caliber.objects.bulk_create(
                [
                    Caliber(name=name, sort_order=index)
                    for index, name in enumerate(
                        ["40-", "40-45", "45-50", "50-55", "55-60", "60-65", "65-70", "70-75", "75-80", "80+"],
                        start=1,
                    )
                ]
            )
        if not Packaging.objects.exists():
            Packaging.objects.bulk_create([Packaging(name="Сетка"), Packaging(name="Коробка")])
        self.stdout.write("Готово")
