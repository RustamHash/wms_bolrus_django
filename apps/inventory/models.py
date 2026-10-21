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


class InventoryDocument(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        COUNTING = "counting", "Подсчёт"
        POSTED = "posted", "Проведён"
        CANCELLED = "cancelled", "Отменён"

    class Scope(models.TextChoices):
        ALL = "all", "Весь склад"
        PRODUCTS = "products", "Выбранные номенклатуры"

    number = models.CharField("Номер", max_length=32, unique=True, null=True)
    doc_date = models.DateField("Дата", db_index=True)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    scope = models.CharField("Режим", max_length=16, choices=Scope.choices, default=Scope.ALL)
    notes = models.TextField("Примечание", blank=True, default="")
    products = models.ManyToManyField(
        "catalog.Product", blank=True, related_name="inventory_documents", verbose_name="Номенклатура"
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="inventory_documents_created", verbose_name="Создал"
    )
    posted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_documents_posted",
        verbose_name="Провёл",
    )
    posted_at = models.DateTimeField("Проведено", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        db_table = "inventory_documents"
        verbose_name = "Инвентаризация"
        verbose_name_plural = "Инвентаризации"
        ordering = ["-doc_date", "-id"]

    def __str__(self):
        return self.number


class InventoryLine(models.Model):
    document = models.ForeignKey(
        InventoryDocument, on_delete=models.CASCADE, related_name="lines", verbose_name="Документ"
    )
    pallet = models.ForeignKey(
        "storage.Pallet",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_lines",
        verbose_name="Паллета",
    )
    lot = models.ForeignKey(
        "receiving.Lot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_lines",
        verbose_name="Партия учёта",
    )
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_found_lines",
        verbose_name="Товар",
    )
    caliber = models.ForeignKey(
        "catalog.Caliber",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_found_lines",
        verbose_name="Калибр",
    )
    grade = models.ForeignKey(
        "catalog.Grade",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_found_lines",
        verbose_name="Сорт",
    )
    supplier = models.ForeignKey(
        "catalog.Supplier", on_delete=models.PROTECT, related_name="inventory_lines", verbose_name="Поставщик"
    )
    location = models.CharField("Место", max_length=120, blank=True, default="")
    book_kg = models.DecimalField("Учёт, кг", max_digits=12, decimal_places=3)
    fact_kg = models.DecimalField("Факт, кг", max_digits=12, decimal_places=3, null=True, blank=True)
    counted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_lines_counted",
        verbose_name="Посчитал",
    )
    counted_at = models.DateTimeField("Посчитано", null=True, blank=True)

    class Meta:
        db_table = "inventory_lines"
        verbose_name = "Строка инвентаризации"
        verbose_name_plural = "Строки инвентаризации"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["document", "pallet"], name="inventory_line_document_pallet"),
        ]

    def __str__(self):
        pallet = self.pallet.number if self.pallet_id else "новая"
        return f"{self.document.number} · {pallet}"

    @property
    def found(self) -> bool:
        return self.pallet_id is None

    @property
    def delta_kg(self):
        if self.fact_kg is None:
            return None
        return self.fact_kg - self.book_kg
