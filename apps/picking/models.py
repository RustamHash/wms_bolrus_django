from django.db import models

from apps.common.kg import format_kg


class PickAllocation(models.Model):
    """План отбора: сколько килограммов строки задания взять с паллеты. Это не движение склада."""

    task_line = models.ForeignKey(
        "planning.TaskLine",
        on_delete=models.CASCADE,
        related_name="pick_allocations",
        verbose_name="Строка задания",
    )
    pallet = models.ForeignKey(
        "storage.Pallet",
        on_delete=models.PROTECT,
        related_name="pick_allocations",
        verbose_name="Паллета",
    )
    qty_kg = models.DecimalField("План, кг", max_digits=12, decimal_places=3)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        db_table = "pick_allocations"
        verbose_name = "Назначение отбора"
        verbose_name_plural = "Назначения отбора"
        constraints = [
            models.UniqueConstraint(fields=["task_line", "pallet"], name="pick_allocation_line_pallet"),
        ]

    def __str__(self):
        pallet = self.pallet.number or self.pallet_id
        return f"{pallet}: {format_kg(self.qty_kg)} кг"
