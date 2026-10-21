from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0012_order_line_grade_manual"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorder",
            name="arrived_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Прибыл"),
        ),
        migrations.AddField(
            model_name="purchaseorder",
            name="gate_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Постановка на ворота"),
        ),
        migrations.AddField(
            model_name="purchaseorder",
            name="receive_finished_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Окончание приёмки"),
        ),
        migrations.AddField(
            model_name="purchaseorder",
            name="departed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Убыл"),
        ),
    ]
