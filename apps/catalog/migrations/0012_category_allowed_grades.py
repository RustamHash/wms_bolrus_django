from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0011_belorechensk_sochi_apsheronsk_routes"),
    ]

    operations = [
        migrations.AddField(
            model_name="consigneecategory",
            name="allowed_grades",
            field=models.ManyToManyField(
                blank=True,
                related_name="allowed_in_categories",
                to="catalog.grade",
                verbose_name="Можно отгружать",
            ),
        ),
    ]
