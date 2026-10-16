from django.db import models


class Printer(models.Model):
    name = models.CharField("Имя", max_length=120)
    printer_type = models.CharField("Тип", max_length=32)
    host = models.CharField("Адрес", max_length=200, blank=True, default="")
    port = models.IntegerField("Порт", default=9100)
    queue_name = models.CharField("Очередь Windows", max_length=200, blank=True, default="")
    label_width_mm = models.IntegerField(default=58)
    label_height_mm = models.IntegerField(default=40)
    gap_mm = models.IntegerField(default=2)
    dpi = models.IntegerField(default=203)
    density = models.IntegerField(default=8)
    is_default = models.BooleanField("По умолчанию", default=False)

    class Meta:
        db_table = "printers"
        verbose_name = "Принтер"
        verbose_name_plural = "Принтеры"

    def __str__(self):
        return self.name
