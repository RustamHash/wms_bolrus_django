from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_one_person"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="employee",
        ),
    ]
