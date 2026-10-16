from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0005_order_line_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorder",
            name="external_number",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True, verbose_name="Номер 1С"),
        ),
    ]
