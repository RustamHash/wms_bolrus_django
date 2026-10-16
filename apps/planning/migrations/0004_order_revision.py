import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("planning", "0003_parties"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="revision",
            field=models.PositiveIntegerField(default=1, verbose_name="Версия"),
        ),
        migrations.CreateModel(
            name="OrderRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField(verbose_name="Версия")),
                ("ship_date", models.DateField(verbose_name="Дата отгрузки")),
                ("client_name", models.CharField(max_length=200, verbose_name="Покупатель")),
                ("consignee_name", models.CharField(blank=True, default="", max_length=200, verbose_name="Грузополучатель")),
                ("seller_name", models.CharField(max_length=200, verbose_name="Продавец")),
                ("shipping_terms", models.TextField(blank=True, default="", verbose_name="Условия отгрузки")),
                ("lines", models.JSONField(default=list, verbose_name="Строки")),
                ("changes", models.JSONField(default=dict, verbose_name="Расхождения")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="order_revisions",
                        to="accounts.user",
                    ),
                ),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="planning.order",
                    ),
                ),
            ],
            options={
                "verbose_name": "Версия заказа",
                "verbose_name_plural": "Версии заказов",
                "db_table": "order_revisions",
                "ordering": ["-revision"],
                "unique_together": {("order", "revision")},
            },
        ),
    ]
