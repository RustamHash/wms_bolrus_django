import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0006_warehouse_cells"),
        ("inventory", "0009_pick_leaves_stock"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryline",
            name="cell",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inventory_lines",
                to="storage.warehousecell",
                verbose_name="Ячейка",
            ),
        ),
    ]
