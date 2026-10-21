from django.db import migrations, models


def mark_chosen_grades(apps, schema_editor):
    OrderLine = apps.get_model("planning", "OrderLine")
    OrderLineGrade = apps.get_model("planning", "OrderLineGrade")
    OrderLine.objects.filter(id__in=OrderLineGrade.objects.values("order_line_id")).update(grade_manual=True)


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0011_task_work_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderline",
            name="grade_manual",
            field=models.BooleanField(default=False, verbose_name="Сорт задан в заказе"),
        ),
        migrations.RunPython(mark_chosen_grades, migrations.RunPython.noop),
    ]
