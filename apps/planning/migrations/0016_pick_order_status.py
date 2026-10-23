from django.db import migrations


def sync_closed_picks(apps, schema_editor):
    from apps.planning.models import Order
    from apps.planning.services import sync_order_pick_status

    orders = Order.objects.filter(tasks__kind="pick", tasks__status="closed").exclude(status="closed").distinct()
    for order in orders:
        sync_order_pick_status(order)


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0015_consignee_only"),
        ("inventory", "0008_consignee_only"),
    ]

    operations = [
        migrations.RunPython(sync_closed_picks, migrations.RunPython.noop),
    ]
