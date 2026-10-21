from django.db import migrations, models
import django.db.models.deletion


def seed_categories(apps, schema_editor):
    Grade = apps.get_model("catalog", "Grade")
    Category = apps.get_model("catalog", "ConsigneeCategory")
    for order, name in ((10, "Экстра"), (20, "Сеть")):
        grade = Grade.objects.filter(name=name).first()
        if grade is None:
            grade = Grade.objects.create(name=name, sort_order=order)
        Category.objects.get_or_create(name=name, defaults={"sort_order": order, "grade_id": grade.id})


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0007_consignee_allowed_grades"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConsigneeCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True, verbose_name="Категория")),
                ("sort_order", models.IntegerField(default=0, verbose_name="Порядок")),
                (
                    "grade",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="consignee_categories",
                        to="catalog.grade",
                        verbose_name="Сортность",
                    ),
                ),
            ],
            options={
                "verbose_name": "Категория грузополучателя",
                "verbose_name_plural": "Категории грузополучателей",
                "db_table": "consignee_categories",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.AddField(
            model_name="consignee",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="consignees",
                to="catalog.consigneecategory",
                verbose_name="Категория",
            ),
        ),
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
    ]
