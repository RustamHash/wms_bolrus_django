from django.db import migrations, models
import django.db.models.deletion


def copy_printers(apps, schema_editor):
    PurchaseOrder = apps.get_model("planning", "PurchaseOrder")
    Task = apps.get_model("planning", "Task")
    for order in PurchaseOrder.objects.exclude(printer_id=None):
        Task.objects.filter(purchase_order_id=order.id, kind="receive", printer_id=None).update(
            printer_id=order.printer_id
        )


class Migration(migrations.Migration):

    dependencies = [
        ("printing", "0001_initial"),
        ("planning", "0020_purchase_onec_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="printer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="printing.printer",
                verbose_name="Принтер",
            ),
        ),
        migrations.RunPython(copy_printers, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="purchaseorder",
            name="printer",
        ),
    ]
