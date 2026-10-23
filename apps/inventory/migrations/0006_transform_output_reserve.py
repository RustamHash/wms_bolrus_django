import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_category_allowed_grades"),
        ("inventory", "0005_transform_document"),
        ("planning", "0014_receive_assignee_printer"),
    ]

    operations = [
        migrations.AddField(
            model_name="transformdocument",
            name="categories_set",
            field=models.BooleanField(default=False, verbose_name="Категории выхода заданы"),
        ),
        migrations.AddField(
            model_name="transformdocument",
            name="allowed_categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="transform_documents",
                to="catalog.consigneecategory",
                verbose_name="Категории выхода",
            ),
        ),
        migrations.AddField(
            model_name="transformdocument",
            name="output_client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transform_outputs_held",
                to="catalog.client",
                verbose_name="Резерв выхода под клиента",
            ),
        ),
        migrations.AddField(
            model_name="transformdocument",
            name="output_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transform_outputs_held",
                to="planning.orderline",
                verbose_name="Резерв выхода под строку",
            ),
        ),
    ]
