import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0013_consignee_only"),
        ("inventory", "0010_line_cell"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryline",
            name="packaging",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inventory_lines",
                to="catalog.packaging",
                verbose_name="Упаковка",
            ),
        ),
        migrations.AddField(
            model_name="inventoryline",
            name="pallet_type",
            field=models.CharField(blank=True, default="", max_length=8, verbose_name="Вид паллета"),
        ),
        migrations.AddField(
            model_name="inventoryline",
            name="places",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Упаковок"),
        ),
        migrations.AddField(
            model_name="inventoryline",
            name="gross_kg",
            field=models.DecimalField(
                blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="Грязный вес, кг"
            ),
        ),
    ]
