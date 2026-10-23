from decimal import Decimal

from django.db import migrations


def drop_picked_from_stock(apps, schema_editor):
    """Отбор уже списал вес с резерва или со свободного. Колонка «Отобрано» держала его в остатке."""
    from apps.inventory.models import Balance, Movement
    from apps.inventory.services import unpicked_kg
    from apps.storage.models import Pallet

    Balance.objects.update(picked_kg=Decimal("0.000"))
    held = Pallet.objects.exclude(reserved_line=None, reserved_consignee=None, order=None)
    for pallet in held.iterator():
        if not Movement.objects.filter(pallet=pallet, kind="in").exists():
            continue
        if unpicked_kg(pallet.id) > 0:
            continue
        pallet.reserved_line = None
        pallet.reserved_consignee = None
        pallet.order = None
        pallet.save(update_fields=["reserved_line", "reserved_consignee", "order"])


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_consignee_only"),
        ("storage", "0005_consignee_only"),
    ]

    operations = [
        migrations.RunPython(drop_picked_from_stock, migrations.RunPython.noop),
    ]
