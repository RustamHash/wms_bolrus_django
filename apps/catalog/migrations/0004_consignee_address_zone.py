import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0003_parties"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeliveryZone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True, verbose_name="Зона доставки")),
            ],
            options={
                "verbose_name": "Зона доставки",
                "verbose_name_plural": "Зоны доставки",
                "db_table": "delivery_zones",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="consignee",
            name="address",
            field=models.TextField(blank=True, default="", verbose_name="Адрес"),
        ),
        migrations.AddField(
            model_name="consignee",
            name="zone",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="consignees",
                to="catalog.deliveryzone",
                verbose_name="Зона доставки",
            ),
        ),
    ]
