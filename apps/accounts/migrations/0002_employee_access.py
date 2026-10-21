from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="access_receive",
            field=models.BooleanField(default=False, verbose_name="Приёмка"),
        ),
        migrations.AddField(
            model_name="user",
            name="access_pick",
            field=models.BooleanField(default=False, verbose_name="Отбор"),
        ),
        migrations.AddField(
            model_name="user",
            name="access_ship",
            field=models.BooleanField(default=False, verbose_name="Отгрузка"),
        ),
        migrations.AddField(
            model_name="user",
            name="access_move",
            field=models.BooleanField(default=False, verbose_name="Перемещение"),
        ),
    ]
