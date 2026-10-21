from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logistics", "0005_route_docked"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="arrived_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Прибыл"),
        ),
        migrations.AddField(
            model_name="route",
            name="departed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Убыл"),
        ),
    ]
