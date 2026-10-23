from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(model_name="shipment", name="units"),
        migrations.DeleteModel(name="ShipmentUnit"),
        migrations.DeleteModel(name="Shipment"),
    ]
