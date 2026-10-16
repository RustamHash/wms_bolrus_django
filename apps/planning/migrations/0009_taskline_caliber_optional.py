import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0006_supplier_code"),
        ("planning", "0008_purchase_line_external"),
    ]

    operations = [
        migrations.AlterField(
            model_name="taskline",
            name="caliber",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="task_lines",
                to="catalog.caliber",
            ),
        ),
        migrations.AlterField(
            model_name="taskline",
            name="grade",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="task_lines",
                to="catalog.grade",
            ),
        ),
    ]
