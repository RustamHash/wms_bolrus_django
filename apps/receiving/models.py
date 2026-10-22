from django.db import models

from apps.common.numbers import document_number


class Receipt(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    supplier = models.ForeignKey("catalog.Supplier", on_delete=models.PROTECT, related_name="receipts")
    doc_date = models.DateField()
    ttn_number = models.CharField(max_length=80, blank=True, default="")
    status = models.CharField(max_length=32, default="open")
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="receipts_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "receipts"
        verbose_name = "Приход"
        verbose_name_plural = "Приходы"

    def __str__(self):
        return self.number or f"#{self.pk}"


class Lot(models.Model):
    class Source(models.TextChoices):
        SUPPLY = "supply", "Поставка"
        INVENTORY = "inventory", "Инвентаризация"
        REWORK = "rework", "Переборка"
        WORKSHOP = "workshop", "Цех"

    number = models.CharField("Номер", max_length=32, unique=True, null=True, blank=True)
    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT, related_name="lots")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        verbose_name="Родитель",
    )
    source = models.CharField(
        "Источник", max_length=16, choices=Source.choices, default=Source.SUPPLY
    )
    supplier = models.ForeignKey("catalog.Supplier", on_delete=models.PROTECT, related_name="lots")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="lots")
    caliber = models.ForeignKey("catalog.Caliber", on_delete=models.PROTECT, related_name="lots")
    grade = models.ForeignKey("catalog.Grade", on_delete=models.PROTECT, related_name="lots")
    received_on = models.DateField("Дата прихода")
    expires_on = models.DateField("Срок годности", null=True, blank=True)
    passport_grade = models.CharField("Сорт с паспорта", max_length=120, blank=True, default="")
    passport_supplier = models.CharField("Поставщик с паспорта", max_length=200, blank=True, default="")
    is_opening = models.BooleanField("Начальный остаток", default=False)

    class Meta:
        db_table = "lots"
        verbose_name = "Партия"
        verbose_name_plural = "Партии"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")),
                name="lot_parent_not_self",
            )
        ]

    def __str__(self):
        label = f"{self.product.name}, {self.caliber.name}, {self.grade.name}, {self.supplier.name}"
        if self.number:
            return f"{self.number}: {label}"
        return label

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.number:
            return
        self.number = document_number("ПТ", self.pk)
        super().save(update_fields=["number"])
