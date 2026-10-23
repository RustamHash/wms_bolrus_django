import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_user_screen_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="employee",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="account",
                to="accounts.user",
                verbose_name="Сотрудник",
            ),
        ),
    ]
