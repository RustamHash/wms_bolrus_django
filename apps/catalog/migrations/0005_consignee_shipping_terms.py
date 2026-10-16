from django.db import migrations, models


def copy_terms(apps, schema_editor):
    Client = apps.get_model("catalog", "Client")
    Consignee = apps.get_model("catalog", "Consignee")
    for client in Client.objects.exclude(shipping_terms=""):
        consignee = Consignee.objects.filter(code=client.code).first() if client.code else None
        if consignee is None:
            consignee = Consignee.objects.filter(name=client.name).first()
        if consignee is None or consignee.shipping_terms:
            continue
        consignee.shipping_terms = client.shipping_terms
        consignee.save(update_fields=["shipping_terms"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_consignee_address_zone"),
    ]

    operations = [
        migrations.AddField(
            model_name="consignee",
            name="shipping_terms",
            field=models.TextField(blank=True, default="", verbose_name="Условия отгрузки"),
        ),
        migrations.RunPython(copy_terms, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="client",
            name="shipping_terms",
        ),
    ]
