import django.db.models.deletion
from django.db import migrations, models


def fill_printers(apps, schema_editor):
    Printer = apps.get_model("printing", "Printer")
    PurchaseOrder = apps.get_model("planning", "PurchaseOrder")
    printer = Printer.objects.filter(printer_type="tsc", is_default=True).first()
    if printer is None:
        printer = Printer.objects.filter(printer_type="tsc").first()
    if printer is None:
        return
    PurchaseOrder.objects.filter(printer__isnull=True).update(printer_id=printer.id)


class Migration(migrations.Migration):

    dependencies = [
        ("printing", "0001_initial"),
        ("planning", "0013_purchase_gate"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorder",
            name="printer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="purchase_orders",
                to="printing.printer",
                verbose_name="Принтер",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="assignee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_tasks",
                to="accounts.user",
                verbose_name="Сотрудник",
            ),
        ),
        migrations.RunPython(fill_printers, migrations.RunPython.noop),
    ]
