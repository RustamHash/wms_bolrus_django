from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_category_allowed_grades"),
        ("storage", "0003_pallet_reserved_client"),
    ]

    operations = [
        migrations.AddField(
            model_name="pallet",
            name="allowed_categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="pallets",
                to="catalog.consigneecategory",
                verbose_name="Можно отгружать категориям",
            ),
        ),
    ]
