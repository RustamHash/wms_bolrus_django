from django.db import migrations

# Очередность из «Темрюк Маршруты по заявке 19.10.2026»: номер строки маршрута — приоритет остановки.
ROUTE_ZONES = {
    "Темрюк маршрут 1": [
        "СОШ №20 Темрюк КШП",
        "СОШ №4 Темрюк КШП",
        "СОШ №3 Темрюк КШП",
        "СОШ №2 Темрюк КШП",
        "СОШ №14 Темрюк КШП",
        "ДС № 1 старший Темрюк",
        "СОШ №13 Темрюк КШП",
        "ДС № 1 младший Темрюк",
        "СОШ №1 Темрюк КШП",
        "СОШ №21 Темрюк КШП",
        "СОШ №10 Темрюк КШП",
        "ДС №44 Темрюк КШП",
    ],
    "Темрюк маршрут 2": [
        "СОШ №5 Темрюк КШП",
        "СОШ №18 Темрюк КШП",
        "ДС № 28 Темрюк КШП",
        "СОШ №12 Темрюк КШП",
        "ДС № 29 Темрюк",
        "СОШ №26 Темрюк КШП",
        "ДС № 30 Темрюк",
        "СОШ №25 Темрюк КШП",
        "ДС № 32 Темрюк",
        "ДС № 31 Темрюк",
        "СОШ №16 Темрюк КШП",
        "СОШ №32 Темрюк КШП",
        "ДС № 37 Темрюк",
        "СОШ №28 Темрюк КШП",
        "СОШ №8 Темрюк КШП",
    ],
}


def assign_route_zones(apps, schema_editor):
    DeliveryZone = apps.get_model("catalog", "DeliveryZone")
    Consignee = apps.get_model("catalog", "Consignee")
    missing = []
    for zone_name, names in ROUTE_ZONES.items():
        zone, _created = DeliveryZone.objects.get_or_create(name=zone_name)
        for index, name in enumerate(names, start=1):
            updated = Consignee.objects.filter(name=name).update(zone_id=zone.id, delivery_order=index)
            if not updated:
                missing.append(name)
    if missing:
        raise RuntimeError("Нет грузополучателей: " + ", ".join(missing))


def clear_route_zones(apps, schema_editor):
    DeliveryZone = apps.get_model("catalog", "DeliveryZone")
    Consignee = apps.get_model("catalog", "Consignee")
    names = [name for items in ROUTE_ZONES.values() for name in items]
    Consignee.objects.filter(name__in=names).update(zone_id=None, delivery_order=0)
    DeliveryZone.objects.filter(name__in=ROUTE_ZONES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0009_consignee_delivery_order"),
    ]

    operations = [
        migrations.RunPython(assign_route_zones, clear_route_zones),
    ]
