from django.db import models


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
    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT, related_name="lots")
    supplier = models.ForeignKey("catalog.Supplier", on_delete=models.PROTECT, related_name="lots")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="lots")
    caliber = models.ForeignKey("catalog.Caliber", on_delete=models.PROTECT, related_name="lots")
    grade = models.ForeignKey("catalog.Grade", on_delete=models.PROTECT, related_name="lots")
    received_on = models.DateField()

    class Meta:
        db_table = "lots"
        verbose_name = "Партия"
        verbose_name_plural = "Партии"

    def __str__(self):
        return f"{self.product.name}, {self.caliber.name}, {self.grade.name}, {self.supplier.name}"
