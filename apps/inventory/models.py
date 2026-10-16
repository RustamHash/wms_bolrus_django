from django.db import models

from apps.common.kg import format_kg


class Movement(models.Model):
    class Kind(models.TextChoices):
        IN = "in", "Приход"
        PICK = "pick", "Отбор"
        OUT = "out", "Отгрузка"

    task = models.ForeignKey(
        "planning.Task", on_delete=models.PROTECT, related_name="movements", verbose_name="Задание"
    )
    task_line = models.ForeignKey(
        "planning.TaskLine", on_delete=models.PROTECT, related_name="movements", verbose_name="Строка задания"
    )
    pallet = models.ForeignKey(
        "storage.Pallet", on_delete=models.PROTECT, related_name="movements", verbose_name="Паллета"
    )
    kind = models.CharField("Вид", max_length=16, choices=Kind.choices)
    lot = models.ForeignKey(
        "receiving.Lot", on_delete=models.PROTECT, related_name="movements", verbose_name="Партия"
    )
    qty_kg = models.DecimalField("Кг", max_digits=12, decimal_places=3)
    source = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="derived", verbose_name="Источник"
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="movements_created", verbose_name="Создал"
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        db_table = "movements"
        verbose_name = "Движение"
        verbose_name_plural = "Движения"
        constraints = [models.UniqueConstraint(fields=["pallet", "kind"], name="movement_pallet_kind")]

    def __str__(self):
        pallet = self.pallet.number or self.pallet_id
        lot = self.lot
        return f"{pallet}: {lot.product.name}, {lot.caliber.name}, {lot.grade.name}, {format_kg(self.qty_kg)} кг"


class Balance(models.Model):
    lot = models.OneToOneField(
        "receiving.Lot", on_delete=models.PROTECT, related_name="balance", verbose_name="Партия"
    )
    free_kg = models.DecimalField("Свободно, кг", max_digits=12, decimal_places=3, default=0)
    reserved_kg = models.DecimalField("В резерве, кг", max_digits=12, decimal_places=3, default=0)
    picked_kg = models.DecimalField("Отобрано, кг", max_digits=12, decimal_places=3, default=0)

    class Meta:
        db_table = "balances"
        verbose_name = "Остаток"
        verbose_name_plural = "Остатки"

    def __str__(self):
        return str(self.lot)
