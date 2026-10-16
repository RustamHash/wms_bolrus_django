from django.db import migrations


def set_work_status(apps, schema_editor):
    Task = apps.get_model("planning", "Task")
    Task.objects.filter(kind="receive", status="open").update(status="to_receive")
    Task.objects.filter(kind="pick", status="open").update(status="to_pick")
    Task.objects.filter(kind="ship", status="open").update(status="to_ship")


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0010_purchase_customer_line"),
    ]

    operations = [
        migrations.RunPython(set_work_status, migrations.RunPython.noop),
    ]
