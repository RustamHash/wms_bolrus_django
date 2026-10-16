from django.apps import AppConfig


class ReceivingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.receiving"
    label = "receiving"
    verbose_name = "Приёмка"
