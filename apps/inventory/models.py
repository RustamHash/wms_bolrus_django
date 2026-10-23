from django.db import models

from apps.common.kg import format_kg


class Movement(models.Model):
    class Kind(models.TextChoices):
        IN = "in", "Приход"
        PICK = "pick", "Отбор"
        OUT = "out", "Отгрузка"
        TRANSFORM_OUT = "transform_out", "В переработку"
        WASTE = "waste", "Отход"

    task = models.ForeignKey(
        "planning.Task",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
        verbose_name="Задание",
    )
    task_line = models.ForeignKey(
        "planning.TaskLine",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
        verbose_name="Строка задания",
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
    transform_document = models.ForeignKey(
        "inventory.TransformDocument",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
        verbose_name="Переработка",
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="movements_created", verbose_name="Создал"
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        db_table = "movements"
        verbose_name = "Движение"
        verbose_name_plural = "Движения"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(task__isnull=False, task_line__isnull=False)
                | models.Q(transform_document__isnull=False),
                name="movement_task_or_transform",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind__in=["transform_out", "waste"]) | models.Q(transform_document__isnull=False),
                name="movement_rework_has_document",
            ),
        ]

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
    cell = models.ForeignKey(
        "storage.WarehouseCell",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_lines",
        verbose_name="Ячейка",
    )
    packaging = models.ForeignKey(
        "catalog.Packaging",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inventory_lines",
        verbose_name="Упаковка",
    )
    pallet_type = models.CharField("Вид паллета", max_length=8, blank=True, default="")
    places = models.PositiveIntegerField("Упаковок", null=True, blank=True)
    gross_kg = models.DecimalField("Грязный вес, кг", max_digits=12, decimal_places=3, null=True, blank=True)
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

    @property
    def place(self) -> str:
        if self.cell_id:
            return self.cell.label()
        return self.location

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


class TransformDocument(models.Model):
    class Kind(models.TextChoices):
        REWORK = "rework", "Переборка"
        WORKSHOP = "workshop", "Цех"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        POSTED = "posted", "Проведён"
        CANCELLED = "cancelled", "Отменён"

    number = models.CharField("Номер", max_length=32, unique=True, null=True)
    kind = models.CharField("Вид", max_length=16, choices=Kind.choices)
    doc_date = models.DateField("Дата", db_index=True)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    tolerance_kg = models.DecimalField("Допуск, кг", max_digits=12, decimal_places=3, default=0)
    ledger_waste_kg = models.DecimalField("Отход в движениях, кг", max_digits=12, decimal_places=3, null=True, blank=True)
    notes = models.TextField("Примечание", blank=True, default="")
    categories_set = models.BooleanField("Категории выхода заданы", default=False)
    allowed_categories = models.ManyToManyField(
        "catalog.ConsigneeCategory",
        blank=True,
        related_name="transform_documents",
        verbose_name="Категории выхода",
    )
    output_consignee = models.ForeignKey(
        "catalog.Consignee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transform_outputs_held",
        verbose_name="Резерв выхода под грузополучателя",
    )
    output_line = models.ForeignKey(
        "planning.OrderLine",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transform_outputs_held",
        verbose_name="Резерв выхода под строку",
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="transform_documents_created", verbose_name="Создал"
    )
    posted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transform_documents_posted",
        verbose_name="Провёл",
    )
    posted_at = models.DateTimeField("Проведено", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        db_table = "transform_documents"
        verbose_name = "Переработка"
        verbose_name_plural = "Переработка"
        ordering = ["-doc_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tolerance_kg__gte=0),
                name="transform_tolerance_nonnegative",
            )
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"


class TransformInput(models.Model):
    document = models.ForeignKey(
        TransformDocument, on_delete=models.CASCADE, related_name="inputs", verbose_name="Документ"
    )
    pallet = models.ForeignKey(
        "storage.Pallet", on_delete=models.PROTECT, related_name="transform_inputs", verbose_name="Паллета"
    )
    lot = models.ForeignKey(
        "receiving.Lot", on_delete=models.PROTECT, related_name="transform_inputs", verbose_name="Партия"
    )
    qty_kg = models.DecimalField("Кг", max_digits=12, decimal_places=3)

    class Meta:
        db_table = "transform_inputs"
        verbose_name = "Вход переработки"
        verbose_name_plural = "Вход переработки"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["document", "pallet"], name="transform_input_document_pallet"),
        ]

    def __str__(self):
        return f"{self.document} · {self.pallet}"


class TransformOutput(models.Model):
    document = models.ForeignKey(
        TransformDocument, on_delete=models.CASCADE, related_name="outputs", verbose_name="Документ"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="transform_outputs", verbose_name="Товар"
    )
    caliber = models.ForeignKey(
        "catalog.Caliber", on_delete=models.PROTECT, related_name="transform_outputs", verbose_name="Калибр"
    )
    grade = models.ForeignKey(
        "catalog.Grade", on_delete=models.PROTECT, related_name="transform_outputs", verbose_name="Сорт"
    )
    qty_kg = models.DecimalField("Кг", max_digits=12, decimal_places=3)
    expires_on = models.DateField("Срок годности", null=True, blank=True)
    pallet = models.ForeignKey(
        "storage.Pallet",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transform_outputs",
        verbose_name="Паллета",
    )
    lot = models.ForeignKey(
        "receiving.Lot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transform_outputs",
        verbose_name="Партия",
    )

    class Meta:
        db_table = "transform_outputs"
        verbose_name = "Выход переработки"
        verbose_name_plural = "Выход переработки"
        ordering = ["id"]

    def __str__(self):
        return f"{self.document} · {self.product}"


class TransformWaste(models.Model):
    document = models.ForeignKey(
        TransformDocument, on_delete=models.CASCADE, related_name="wastes", verbose_name="Документ"
    )
    qty_kg = models.DecimalField("Кг", max_digits=12, decimal_places=3)
    reason = models.CharField("Причина", max_length=200, blank=True, default="")

    class Meta:
        db_table = "transform_wastes"
        verbose_name = "Отход переработки"
        verbose_name_plural = "Отход переработки"
        ordering = ["id"]

    def __str__(self):
        return f"{self.document} · {self.qty_kg}"
