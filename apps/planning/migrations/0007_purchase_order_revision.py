import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("planning", "0006_purchase_external_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorder",
            name="revision",
            field=models.PositiveIntegerField(default=1, verbose_name="Версия"),
        ),
        migrations.CreateModel(
            name="PurchaseOrderRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField(verbose_name="Версия")),
                ("doc_date", models.DateField(verbose_name="Дата")),
                ("receipt_date", models.DateField(blank=True, null=True, verbose_name="Дата поступления")),
                ("supplier_name", models.CharField(max_length=200, verbose_name="Поставщик")),
                ("status", models.CharField(max_length=32, verbose_name="Состояние")),
                ("comment", models.TextField(blank=True, default="", verbose_name="Комментарий")),
                ("lines", models.JSONField(default=list, verbose_name="Строки")),
                ("changes", models.JSONField(default=dict, verbose_name="Расхождения")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_order_revisions",
                        to="accounts.user",
                    ),
                ),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="planning.purchaseorder",
                    ),
                ),
            ],
            options={
                "verbose_name": "Версия заказа поставщику",
                "verbose_name_plural": "Версии заказов поставщикам",
                "db_table": "purchase_order_revisions",
                "ordering": ["-revision"],
                "unique_together": {("order", "revision")},
            },
        ),
    ]
