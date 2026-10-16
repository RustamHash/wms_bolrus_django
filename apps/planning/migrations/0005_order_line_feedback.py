from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0004_order_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderline",
            name="for_task",
            field=models.BooleanField(default=True, verbose_name="В задание"),
        ),
        migrations.AddField(
            model_name="orderline",
            name="unit",
            field=models.CharField(default="кг", max_length=16, verbose_name="Ед."),
        ),
    ]
