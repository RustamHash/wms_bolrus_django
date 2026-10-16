from decimal import Decimal

from django.db import migrations, models


def move_named_pallets(apps, schema_editor):
    Movement = apps.get_model("inventory", "Movement")
    Balance = apps.get_model("inventory", "Balance")
    Pallet = apps.get_model("storage", "Pallet")
    arrivals = Movement.objects.filter(kind="in").select_related("pallet", "task_line")
    for movement in arrivals:
        pallet = movement.pallet
        if pallet.order_id is None:
            continue
        if Movement.objects.filter(pallet_id=pallet.id, kind__in=("pick", "out")).exists():
            continue
        if movement.task_line_id and movement.task_line.order_line_id and pallet.reserved_line_id is None:
            Pallet.objects.filter(pk=pallet.id).update(reserved_line_id=movement.task_line.order_line_id)
        balance = Balance.objects.filter(lot_id=movement.lot_id).first()
        if balance is None:
            continue
        qty = movement.qty_kg if balance.free_kg >= movement.qty_kg else balance.free_kg
        if qty <= 0:
            continue
        balance.free_kg = balance.free_kg - qty
        balance.reserved_kg = (balance.reserved_kg or Decimal("0")) + qty
        balance.save(update_fields=["free_kg", "reserved_kg"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
        ("storage", "0002_pallet_reserved_line"),
    ]

    operations = [
        migrations.AddField(
            model_name="balance",
            name="reserved_kg",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12, verbose_name="В резерве, кг"),
        ),
        migrations.RunPython(move_named_pallets, migrations.RunPython.noop),
    ]
