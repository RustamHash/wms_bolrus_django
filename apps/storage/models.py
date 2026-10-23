from django.db import models


class WarehouseZone(models.Model):
    code = models.CharField("Код", max_length=32, unique=True)
    name = models.CharField("Название", max_length=120, unique=True)
    barcode = models.CharField("QR", max_length=64, unique=True)

    class Meta:
        db_table = "warehouse_zones"
        verbose_name = "Зона склада"
        verbose_name_plural = "Зоны склада"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"


def cell_address(zone_code: str, code: str) -> str:
    zone = (zone_code or "").strip()
    place = (code or "").strip()
    if not zone:
        return place[:120]
    prefix = f"{zone}-"
    if place.casefold().startswith(prefix.casefold()):
        return place[:120]
    return f"{zone}-{place}"[:120]


class WarehouseCell(models.Model):
    zone = models.ForeignKey(WarehouseZone, on_delete=models.PROTECT, related_name="cells", verbose_name="Зона")
    code = models.CharField("Код", max_length=32)
    name = models.CharField("Название", max_length=120, blank=True, default="")
    barcode = models.CharField("QR", max_length=64, unique=True)

    class Meta:
        db_table = "warehouse_cells"
        verbose_name = "Ячейка"
        verbose_name_plural = "Ячейки"
        ordering = ["zone__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["zone", "code"], name="warehouse_cell_zone_code"),
        ]

    def label(self) -> str:
        return cell_address(self.zone.code, self.code)

    def __str__(self) -> str:
        return self.label()


class Pallet(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    barcode = models.CharField(max_length=32, unique=True, null=True, blank=True)
    kind = models.CharField(max_length=32)
    status = models.CharField(max_length=32, default="open")
    location = models.CharField(max_length=120, blank=True, default="")
    cell = models.ForeignKey(
        WarehouseCell,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pallets",
        verbose_name="Ячейка",
    )
    pallet_type = models.CharField(max_length=8, blank=True, default="")
    order = models.ForeignKey("planning.Order", null=True, blank=True, on_delete=models.PROTECT, related_name="pallets")
    reserved_consignee = models.ForeignKey(
        "catalog.Consignee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="held_pallets",
        verbose_name="Резерв под грузополучателя",
    )
    reserved_line = models.ForeignKey(
        "planning.OrderLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="reserved_pallets"
    )
    allowed_categories = models.ManyToManyField(
        "catalog.ConsigneeCategory",
        blank=True,
        related_name="pallets",
        verbose_name="Можно отгружать категориям",
    )
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="pallets_created")
    closed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="pallets_closed"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pallets"
        verbose_name = "Паллета"
        verbose_name_plural = "Паллеты"

    def __str__(self):
        return self.number or self.barcode or f"#{self.pk}"


class HandlingUnit(models.Model):
    barcode = models.CharField(max_length=32, unique=True, null=True, blank=True)
    receipt = models.ForeignKey("receiving.Receipt", on_delete=models.PROTECT, related_name="units")
    lot = models.ForeignKey("receiving.Lot", on_delete=models.PROTECT, related_name="units")
    packaging = models.ForeignKey("catalog.Packaging", on_delete=models.PROTECT, related_name="units")
    gross_kg = models.DecimalField(max_digits=12, decimal_places=3)
    net_kg = models.DecimalField(max_digits=12, decimal_places=3)
    grade = models.ForeignKey("catalog.Grade", on_delete=models.PROTECT, related_name="units")
    pallet = models.ForeignKey(Pallet, null=True, blank=True, on_delete=models.PROTECT, related_name="units")
    status = models.CharField(max_length=32, default="received", db_index=True)
    reject_reason = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "handling_units"
        verbose_name = "Место"
        verbose_name_plural = "Места"

    def __str__(self):
        return self.barcode or f"#{self.pk}"
