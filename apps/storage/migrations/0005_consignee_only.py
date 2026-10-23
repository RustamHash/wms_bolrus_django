import django.db.models.deletion
from django.db import migrations, models


def copy_pallet_reserve(apps, schema_editor):
    Pallet = apps.get_model("storage", "Pallet")
    Consignee = apps.get_model("catalog", "Consignee")
    Client = apps.get_model("catalog", "Client")
    for pallet in Pallet.objects.exclude(reserved_client_id=None):
        client = Client.objects.filter(pk=pallet.reserved_client_id).first()
        if client is None:
            continue
        consignee = Consignee.objects.filter(code=client.code).first() if client.code else None
        if consignee is None:
            consignee = Consignee.objects.filter(name=client.name).first()
        if consignee is None:
            consignee = Consignee.objects.create(name=client.name, code=client.code or None)
        pallet.reserved_consignee_id = consignee.id
        pallet.save(update_fields=["reserved_consignee"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_category_allowed_grades"),
        ("storage", "0004_pallet_allowed_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="pallet",
            name="reserved_consignee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="held_pallets",
                to="catalog.consignee",
                verbose_name="Резерв под грузополучателя",
            ),
        ),
        migrations.RunPython(copy_pallet_reserve, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="pallet",
            name="reserved_client",
        ),
    ]
