from django.db import migrations


def fold_employees(apps, schema_editor):
    from apps.accounts.access import sync_user_screens
    from apps.accounts.models import User

    flags = (
        ("receive", "tsd_receive"),
        ("pick", "tsd_pick"),
        ("ship", "tsd_ship"),
        ("move", "tsd_move"),
    )
    for user in User.objects.filter(role="employee").select_related("position"):
        screens = set()
        position = user.position if user.position_id and user.position.is_active else None
        for flag, screen in flags:
            if getattr(user, f"access_{flag}") or (position is not None and getattr(position, f"access_{flag}")):
                screens.add(screen)
        if position is not None and position.access_shift:
            screens.add("tsd_shift")
        if screens:
            screens.add("tsd_find")
        if "tsd_receive" in screens and not screens & {"tsd_pick", "tsd_ship"}:
            user.role = "receiver"
        elif screens - {"tsd_find", "tsd_shift"}:
            user.role = "warehouse"
        else:
            user.role = "none"
        user.save(update_fields=["role"])
        grants = {(screen, action) for screen in screens for action in ("view", "add", "change", "delete")}
        sync_user_screens(user, grants)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_user_employee"),
    ]

    operations = [
        migrations.RunPython(fold_employees, migrations.RunPython.noop),
    ]
