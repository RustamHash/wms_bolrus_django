import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0007_consignee_allowed_grades"),
        ("storage", "0002_pallet_reserved_line"),
    ]

    operations = [
        migrations.AddField(
            model_name="pallet",
            name="reserved_client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="held_pallets",
                to="catalog.client",
                verbose_name="Резерв под клиента",
            ),
        ),
    ]
