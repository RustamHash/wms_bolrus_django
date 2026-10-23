from django.db import models


class Allocation(models.Model):
    unit = models.OneToOneField("storage.HandlingUnit", on_delete=models.CASCADE, related_name="allocation")
    order_line = models.ForeignKey("planning.OrderLine", on_delete=models.PROTECT, related_name="allocations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "allocations"

    def __str__(self):
        return f"{self.unit} → {self.order_line}"

