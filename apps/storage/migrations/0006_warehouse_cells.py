import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0005_consignee_only"),
    ]

    operations = [
        migrations.CreateModel(
            name="WarehouseZone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=32, unique=True, verbose_name="Код")),
                ("name", models.CharField(max_length=120, unique=True, verbose_name="Название")),
                ("barcode", models.CharField(max_length=64, unique=True, verbose_name="QR")),
            ],
            options={
                "verbose_name": "Зона склада",
                "verbose_name_plural": "Зоны склада",
                "db_table": "warehouse_zones",
                "ordering": ["code"],
            },
        ),
        migrations.CreateModel(
            name="WarehouseCell",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=32, verbose_name="Код")),
                ("name", models.CharField(blank=True, default="", max_length=120, verbose_name="Название")),
                ("barcode", models.CharField(max_length=64, unique=True, verbose_name="QR")),
                (
                    "zone",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cells",
                        to="storage.warehousezone",
                        verbose_name="Зона",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ячейка",
                "verbose_name_plural": "Ячейки",
                "db_table": "warehouse_cells",
                "ordering": ["zone__code", "code"],
            },
        ),
        migrations.AddConstraint(
            model_name="warehousecell",
            constraint=models.UniqueConstraint(fields=("zone", "code"), name="warehouse_cell_zone_code"),
        ),
        migrations.AddField(
            model_name="pallet",
            name="cell",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pallets",
                to="storage.warehousecell",
                verbose_name="Ячейка",
            ),
        ),
    ]
