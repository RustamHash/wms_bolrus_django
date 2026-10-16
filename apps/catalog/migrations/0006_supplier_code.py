from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0005_consignee_shipping_terms"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="code",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True, verbose_name="Код"),
        ),
    ]
