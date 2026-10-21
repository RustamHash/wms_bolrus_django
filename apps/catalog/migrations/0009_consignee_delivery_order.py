from django.db import migrations, models


# Порядок остановок из маршрутных листов 19.10.2026.
ROUTE_ZONES = {
    "Сады Краснодар маршрут 1": [
        "ДЕТСКИЙ САД № 135 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 135 МБДОУ г. Краснодар (филиал)",
        "ДЕТСКИЙ САД № 197 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 82 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 33 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 111 КРАСНОДАР основной",
        "ДЕТСКИЙ САД № 111 КРАСНОДАР филиал",
        "ДЕТСКИЙ САД № 66 МАДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 3 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 236 МАДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 79 г. Краснодар",
        "ДЕТСКИЙ САД № 161 МБДОУ МО Г. КРАСНОДАР",
        'ДЕТСКИЙ САД № 231" филиал',
    ],
    "Сады Краснодар маршрут 2": [
        "ДЕТСКИЙ САД № 75 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 208 МАДОУ МО Г.КРАСНОДАР",
        "ДЕТСКИЙ САД № 102 МАДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 166 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 184 (основной)",
        "ДЕТСКИЙ САД № 184 (филиал)",
        "ДЕТСКИЙ САД № 190 МАДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 183 МБДОУ МО Г. КРАСНОДАР",
        "ДЕТСКИЙ САД № 211 МАДОУ МО Г. КРАСНОДАР",
        'ДЕТСКИЙ САД № 201" основной',
        "ДЕТСКИЙ САД № 201 МАДОУ МО Г. КРАСНОДАР филиал",
        "ДЕТСКИЙ САД № 185 МБДОУ МО Г. КРАСНОДАР",
        'ДЕТСКИЙ САД № 231" основной',
    ],
    "Геленджик": [
        "ДС №24 г. Геленджик",
        "ДС №22 г. Геленджик",
        "ООШ №24 г. Геленджик",
        "ДС №19 Геленджик",
        "ООШ №21 Геленджик",
        "ООШ №19 г. Геленджик",
        "ДС №16 г. Геленджик",
        "Д/С №12 Геленджик",
        "СОШ №6 Геленджик",
        "ДС №30 Геленджик",
        "ДС №35 г. Геленджик",
        "ДС №32 г. Геленджик",
        "Д/С №3 Геленджик",
        "ДС №34 г. Геленджик",
        "ДС № 1 Геленджик",
        "СОШ №5 г. Геленджик",
        "Д/С №31 Геленджик",
        "СОШ №2 г. Геленджик",
        "СОШ №5 г. Геленджик (Ф)",
        "СОШ №3 г. Геленджик",
        "СОШ №1 Геленджик",
        "Д/С №2 Геленджик",
        "ДС №28 г. Геленджик",
        "Д/С №2 Геленджик (Верхняя)",
        "СОШ №8 Геленджик",
        "ДС №37 г. Геленджик",
        "Д/С №13 Геленджик",
        "Д/С №7 Геленджик",
    ],
}


def assign_route_zones(apps, schema_editor):
    DeliveryZone = apps.get_model("catalog", "DeliveryZone")
    Consignee = apps.get_model("catalog", "Consignee")
    for zone_name, names in ROUTE_ZONES.items():
        zone, _created = DeliveryZone.objects.get_or_create(name=zone_name)
        for index, name in enumerate(names, start=1):
            Consignee.objects.filter(name=name).update(zone_id=zone.id, delivery_order=index)


def clear_delivery_order(apps, schema_editor):
    Consignee = apps.get_model("catalog", "Consignee")
    names = [name for names in ROUTE_ZONES.values() for name in names]
    Consignee.objects.filter(name__in=names).update(delivery_order=0)


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0008_consignee_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="consignee",
            name="delivery_order",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Порядок остановки при сборке маршрута. 0 — без очереди, точка встаёт в конец.",
                verbose_name="Очередность в зоне",
            ),
        ),
        migrations.RunPython(assign_route_zones, clear_delivery_order),
    ]
