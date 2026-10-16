import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0006_supplier_code"),
        ("planning", "0007_purchase_order_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorderline",
            name="external_code",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="external_name",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="external_product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_lines",
                to="catalog.externalproduct",
            ),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="for_task",
            field=models.BooleanField(default=True, verbose_name="В задание"),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="unit",
            field=models.CharField(default="кг", max_length=16, verbose_name="Ед."),
        ),
        migrations.AlterField(
            model_name="purchaseorderline",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_lines",
                to="catalog.product",
            ),
        ),
    ]
