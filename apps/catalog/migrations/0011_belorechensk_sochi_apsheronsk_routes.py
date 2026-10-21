from django.db import migrations

# Очередность из маршрутных листов 19.10 и 20.10: номер строки — приоритет остановки.
ROUTE_ZONES = {
    "Белореченск маршрут 1": [
        "СОШ 21 Белореченский",
        "СОШ 18 Белореченск",
        "СОШ 16 Белореченск",
        "СОШ 29 Белореченск",
        "СОШ 30 Белореченск",
        "СОШ 25 Белореченск",
        "СОШ 26 Белореченск",
        "СОШ 7 Белореченск",
        "СОШ 31 Белореченск (Вита Лайн)",
        "СОШ 4 Белореченск",
        "СОШ 36 Белореченск",
        "СОШ 12 Белореченск",
    ],
    "Сочи маршрут 1": [
        "ООШ 55 Сочи",
        "СОШ 2 Сочи",
        "НОШ-ДС № 85 Сочи",
        "НОШ-ДС № 80 Сочи",
        "СОШ 96 Сочи",
        "СОШ 90 Сочи",
        "СОШ 92 Сочи",
        "СОШ 79 Сочи",
    ],
}

# Зона уже есть и совпадает с листом «Маршрут 1 Апшеронск». Меняется только очередь.
EXISTING_ORDERS = {
    "Апшеронск 1 Школы": [
        "ВИТА ЛАЙН ООО СОШ 17 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 16 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 10 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 37 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 18 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 4 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 30 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 3 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 2 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 1 Апшеронск",
        "ВИТА ЛАЙН ООО СОШ 5 Апшеронск",
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
    for zone_name, names in EXISTING_ORDERS.items():
        zone = DeliveryZone.objects.filter(name=zone_name).first()
        if zone is None:
            missing.append(zone_name)
            continue
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
    kept = [name for items in EXISTING_ORDERS.values() for name in items]
    Consignee.objects.filter(name__in=kept).update(delivery_order=0)


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0010_temryuk_route_zones"),
    ]

    operations = [
        migrations.RunPython(assign_route_zones, clear_route_zones),
    ]
