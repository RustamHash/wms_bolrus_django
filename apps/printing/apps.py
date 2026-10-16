from django.apps import AppConfig


class PrintingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.printing"
    label = "printing"
    verbose_name = "Печать"
