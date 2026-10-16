import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0009_taskline_caliber_optional"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorderline",
            name="customer_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="supply_lines",
                to="planning.orderline",
            ),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="reserve_kg",
            field=models.DecimalField(
                blank=True, decimal_places=3, max_digits=12, null=True, verbose_name="План резерва, кг"
            ),
        ),
    ]
