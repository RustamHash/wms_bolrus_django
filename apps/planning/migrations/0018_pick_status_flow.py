from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def _kg(value) -> Decimal:
    return Decimal(value or 0)


def retitle_pick_statuses(apps, schema_editor):
    Task = apps.get_model("planning", "Task")
    TaskLine = apps.get_model("planning", "TaskLine")
    Movement = apps.get_model("inventory", "Movement")
    PickAllocation = apps.get_model("picking", "PickAllocation")
    for task in Task.objects.filter(kind="pick").iterator():
        lines = list(TaskLine.objects.filter(task_id=task.pk))
        facts = {}
        if lines:
            rows = (
                Movement.objects.filter(kind="pick", task_line_id__in=[line.id for line in lines])
                .values("task_line_id")
                .annotate(total=Sum("qty_kg"))
            )
            facts = {row["task_line_id"]: _kg(row["total"]) for row in rows}
        all_done = bool(lines) and all(_kg(line.qty_kg) <= facts.get(line.id, Decimal("0")) for line in lines)
        if task.status == "closed" and not all_done:
            continue
        any_fact = any(facts.get(line.id, Decimal("0")) > 0 for line in lines)
        any_plan = PickAllocation.objects.filter(task_line__task_id=task.pk).exists()
        if all_done:
            new = "picked"
        elif any_fact:
            new = "in_work"
        elif any_plan:
            new = "planned"
        else:
            new = "created"
        if task.status != new:
            task.status = new
            task.save(update_fields=["status"])


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0017_receive_assigned_status"),
        ("inventory", "0009_pick_leaves_stock"),
        ("picking", "0001_pick_kg_plan"),
    ]

    operations = [
        migrations.RunPython(retitle_pick_statuses, migrations.RunPython.noop),
    ]
