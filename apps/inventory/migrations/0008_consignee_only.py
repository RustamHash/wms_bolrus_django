import django.db.models.deletion
from django.db import migrations, models


def copy_output_reserve(apps, schema_editor):
    Document = apps.get_model("inventory", "TransformDocument")
    Consignee = apps.get_model("catalog", "Consignee")
    Client = apps.get_model("catalog", "Client")
    for document in Document.objects.exclude(output_client_id=None):
        client = Client.objects.filter(pk=document.output_client_id).first()
        if client is None:
            continue
        consignee = Consignee.objects.filter(code=client.code).first() if client.code else None
        if consignee is None:
            consignee = Consignee.objects.filter(name=client.name).first()
        if consignee is None:
            consignee = Consignee.objects.create(name=client.name, code=client.code or None)
        document.output_consignee_id = consignee.id
        document.save(update_fields=["output_consignee"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_category_allowed_grades"),
        ("inventory", "0007_pick_kg_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="transformdocument",
            name="output_consignee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transform_outputs_held",
                to="catalog.consignee",
                verbose_name="Резерв выхода под грузополучателя",
            ),
        ),
        migrations.RunPython(copy_output_reserve, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="transformdocument",
            name="output_client",
        ),
    ]
