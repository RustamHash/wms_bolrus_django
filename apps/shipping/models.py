from django.db import models


class Allocation(models.Model):
    unit = models.OneToOneField("storage.HandlingUnit", on_delete=models.CASCADE, related_name="allocation")
    order_line = models.ForeignKey("planning.OrderLine", on_delete=models.PROTECT, related_name="allocations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "allocations"

    def __str__(self):
        return f"{self.unit} → {self.order_line}"


class Shipment(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    order = models.ForeignKey("planning.Order", on_delete=models.PROTECT, related_name="shipments")
    task = models.ForeignKey("planning.Task", null=True, blank=True, on_delete=models.PROTECT, related_name="shipments")
    seller_name = models.CharField(max_length=200)
    seller_requisites = models.TextField(blank=True, default="")
    client_name = models.CharField(max_length=200)
    shipping_terms = models.TextField(blank=True, default="")
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="shipments_created")
    created_at = models.DateTimeField(auto_now_add=True)
    units = models.ManyToManyField("storage.HandlingUnit", through="ShipmentUnit", related_name="shipments")

    class Meta:
        db_table = "shipments"
        verbose_name = "Накладная"
        verbose_name_plural = "Накладные"

    def __str__(self):
        return self.number or f"#{self.pk}"


class ShipmentUnit(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE)
    unit = models.ForeignKey("storage.HandlingUnit", on_delete=models.PROTECT)

    class Meta:
        db_table = "shipment_units"
        unique_together = (("shipment", "unit"),)

    def __str__(self):
        return f"{self.shipment} · {self.unit}"
