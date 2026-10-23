from django.db import migrations


def use_onec_numbers(apps, schema_editor):
    PurchaseOrder = apps.get_model("planning", "PurchaseOrder")
    taken = set(PurchaseOrder.objects.values_list("number", flat=True))
    for order in PurchaseOrder.objects.exclude(external_number__isnull=True).exclude(external_number=""):
        external = (order.external_number or "").strip()
        if not external or order.number == external or external in taken:
            continue
        taken.discard(order.number)
        taken.add(external)
        order.number = external
        order.save(update_fields=["number"])


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0019_pick_partial_plan"),
    ]

    operations = [
        migrations.RunPython(use_onec_numbers, migrations.RunPython.noop),
    ]
