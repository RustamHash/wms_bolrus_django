from django.db import migrations


def mark_receive_progress(apps, schema_editor):
    Task = apps.get_model("planning", "Task")
    Movement = apps.get_model("inventory", "Movement")
    started = Movement.objects.filter(kind="in").exclude(task_id=None).values("task_id")
    (
        Task.objects.filter(kind="receive", status__in=["open", "to_receive", "assigned"], pk__in=started)
        .update(status="in_work")
    )
    (
        Task.objects.filter(kind="receive", status__in=["open", "to_receive"], assignee__isnull=False)
        .exclude(pk__in=started)
        .update(status="assigned")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0016_pick_order_status"),
        ("inventory", "0009_pick_leaves_stock"),
    ]

    operations = [
        migrations.RunPython(mark_receive_progress, migrations.RunPython.noop),
    ]
