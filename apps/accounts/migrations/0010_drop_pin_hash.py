from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_drop_user_employee"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="pin_hash",
        ),
    ]
