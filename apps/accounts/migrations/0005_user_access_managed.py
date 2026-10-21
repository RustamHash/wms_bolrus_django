from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_employee_fio"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="access_managed",
            field=models.BooleanField(default=False, verbose_name="Доступы заданы в карточке"),
        ),
    ]
