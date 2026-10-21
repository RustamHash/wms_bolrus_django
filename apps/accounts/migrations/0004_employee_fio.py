from django.db import migrations, models


def fill_fio(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(role="employee"):
        parts = (user.name or "").split()
        if len(parts) >= 3:
            user.last_name = parts[0]
            user.first_name = parts[1]
            user.middle_name = " ".join(parts[2:])
        elif len(parts) == 2:
            user.last_name = parts[0]
            user.first_name = parts[1]
        elif len(parts) == 1:
            user.last_name = parts[0]
        user.save(update_fields=["last_name", "first_name", "middle_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_position"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_name",
            field=models.CharField(blank=True, default="", max_length=80, verbose_name="Фамилия"),
        ),
        migrations.AddField(
            model_name="user",
            name="first_name",
            field=models.CharField(blank=True, default="", max_length=80, verbose_name="Имя"),
        ),
        migrations.AddField(
            model_name="user",
            name="middle_name",
            field=models.CharField(blank=True, default="", max_length=80, verbose_name="Отчество"),
        ),
        migrations.AddField(
            model_name="user",
            name="tsd_code",
            field=models.CharField(blank=True, max_length=8, null=True, unique=True, verbose_name="Короткий код"),
        ),
        migrations.RunPython(fill_fio, migrations.RunPython.noop),
    ]
