from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def mark_partial_plans(apps, schema_editor):
    Task = apps.get_model("planning", "Task")
    TaskLine = apps.get_model("planning", "TaskLine")
    PickAllocation = apps.get_model("picking", "PickAllocation")
    for task in Task.objects.filter(kind="pick", status="planned").iterator():
        lines = list(TaskLine.objects.filter(task_id=task.pk))
        rows = (
            PickAllocation.objects.filter(task_line__task_id=task.pk)
            .values("task_line_id")
            .annotate(total=Sum("qty_kg"))
        )
        planned = {row["task_line_id"]: Decimal(row["total"] or 0) for row in rows}
        if not planned:
            continue
        if any(planned.get(line.id, Decimal("0")) < Decimal(line.qty_kg or 0) for line in lines):
            task.status = "partial"
            task.save(update_fields=["status"])


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0018_pick_status_flow"),
        ("picking", "0001_pick_kg_plan"),
    ]

    operations = [
        migrations.RunPython(mark_partial_plans, migrations.RunPython.noop),
    ]
