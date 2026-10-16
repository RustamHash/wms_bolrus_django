import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0010_purchase_customer_line"),
        ("storage", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pallet",
            name="reserved_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reserved_pallets",
                to="planning.orderline",
            ),
        ),
    ]
