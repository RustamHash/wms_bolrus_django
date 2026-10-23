from django.db import models

from apps.catalog.models import ProductAlias
from apps.common.constants import TASK_WORK_STATUS
from apps.common.kg import format_kg
from apps.common.present import describe_line


class Order(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    external_number = models.CharField("Номер 1С", max_length=32, unique=True, null=True, blank=True)
    consignee = models.ForeignKey(
        "catalog.Consignee", on_delete=models.PROTECT, related_name="orders", verbose_name="Грузополучатель"
    )
    seller = models.ForeignKey("catalog.Seller", on_delete=models.PROTECT, related_name="orders")
    ship_date = models.DateField()
    shipping_terms = models.TextField(blank=True, default="")
    status = models.CharField(max_length=32, default="open")
    revision = models.PositiveIntegerField("Версия", default=1)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="orders_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders"
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"

    def __str__(self):
        return self.number or f"#{self.pk}"


class OrderRevision(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="revisions")
    revision = models.PositiveIntegerField("Версия")
    ship_date = models.DateField("Дата отгрузки")
    client_name = models.CharField("Покупатель", max_length=200)
    consignee_name = models.CharField("Грузополучатель", max_length=200, blank=True, default="")
    seller_name = models.CharField("Продавец", max_length=200)
    shipping_terms = models.TextField("Условия отгрузки", blank=True, default="")
    lines = models.JSONField("Строки", default=list)
    changes = models.JSONField("Расхождения", default=dict)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="order_revisions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_revisions"
        verbose_name = "Версия заказа"
        verbose_name_plural = "Версии заказов"
        unique_together = (("order", "revision"),)
        ordering = ["-revision"]

    def __str__(self):
        return f"{self.order} v{self.revision}"


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="order_lines"
    )
    external_product = models.ForeignKey(
        "catalog.ExternalProduct", null=True, blank=True, on_delete=models.PROTECT, related_name="order_lines"
    )
    external_code = models.CharField(max_length=32, blank=True, default="")
    external_name = models.CharField(max_length=240, blank=True, default="")
    unit = models.CharField("Ед.", max_length=16, default="кг")
    for_task = models.BooleanField("В задание", default=True)
    caliber_mode = models.CharField(max_length=16, default="any")
    caliber = models.ForeignKey(
        "catalog.Caliber", null=True, blank=True, on_delete=models.PROTECT, related_name="exact_lines"
    )
    caliber_from = models.ForeignKey(
        "catalog.Caliber", null=True, blank=True, on_delete=models.PROTECT, related_name="range_from_lines"
    )
    caliber_to = models.ForeignKey(
        "catalog.Caliber", null=True, blank=True, on_delete=models.PROTECT, related_name="range_to_lines"
    )
    qty_kg = models.DecimalField(max_digits=12, decimal_places=3)
    grades = models.ManyToManyField("catalog.Grade", through="OrderLineGrade", related_name="order_lines")
    grade_manual = models.BooleanField("Сорт задан в заказе", default=False)

    class Meta:
        db_table = "order_lines"
        verbose_name = "Строка заказа"
        verbose_name_plural = "Строки заказов"

    def external_title(self) -> str:
        code = self.external_code
        name = self.external_name
        if self.external_product_id:
            if not code:
                code = self.external_product.code
            if not name:
                name = self.external_product.name
        if code and name:
            return f"{code} · {name}"
        return name or code or ""

    def warehouse_product(self):
        if self.external_product_id:
            try:
                return self.external_product.alias.product
            except ProductAlias.DoesNotExist:
                return None
        if self.product_id:
            return self.product
        return None

    def product_title(self) -> str:
        product = self.warehouse_product()
        if product is not None:
            return product.name
        return self.external_title() or "без товара"

    def __str__(self):
        return f"{describe_line(self)}, {format_kg(self.qty_kg)} кг"


class OrderLineGrade(models.Model):
    order_line = models.ForeignKey(OrderLine, on_delete=models.CASCADE)
    grade = models.ForeignKey("catalog.Grade", on_delete=models.PROTECT)

    class Meta:
        db_table = "order_line_grades"
        unique_together = (("order_line", "grade"),)


class PurchaseOrder(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    external_number = models.CharField("Номер 1С", max_length=32, unique=True, null=True, blank=True)
    doc_date = models.DateField()
    supplier = models.ForeignKey("catalog.Supplier", on_delete=models.PROTECT, related_name="purchase_orders")
    receipt_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=32, default="draft")
    comment = models.TextField(blank=True, default="")
    arrived_at = models.DateTimeField("Прибыл", null=True, blank=True)
    gate_at = models.DateTimeField("Постановка на ворота", null=True, blank=True)
    receive_finished_at = models.DateTimeField("Окончание приёмки", null=True, blank=True)
    departed_at = models.DateTimeField("Убыл", null=True, blank=True)
    printer = models.ForeignKey(
        "printing.Printer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="purchase_orders",
        verbose_name="Принтер",
    )
    revision = models.PositiveIntegerField("Версия", default=1)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="purchase_orders_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "purchase_orders"
        verbose_name = "Заказ поставщику"
        verbose_name_plural = "Заказы поставщикам"

    def __str__(self):
        return self.number or f"#{self.pk}"


class PurchaseOrderRevision(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="revisions")
    revision = models.PositiveIntegerField("Версия")
    doc_date = models.DateField("Дата")
    receipt_date = models.DateField("Дата поступления", null=True, blank=True)
    supplier_name = models.CharField("Поставщик", max_length=200)
    status = models.CharField("Состояние", max_length=32)
    comment = models.TextField("Комментарий", blank=True, default="")
    lines = models.JSONField("Строки", default=list)
    changes = models.JSONField("Расхождения", default=dict)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="purchase_order_revisions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "purchase_order_revisions"
        verbose_name = "Версия заказа поставщику"
        verbose_name_plural = "Версии заказов поставщикам"
        unique_together = (("order", "revision"),)
        ordering = ["-revision"]

    def __str__(self):
        return f"{self.order} v{self.revision}"


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines", db_column="purchase_order_id")
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_lines"
    )
    external_product = models.ForeignKey(
        "catalog.ExternalProduct", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_lines"
    )
    external_code = models.CharField(max_length=32, blank=True, default="")
    external_name = models.CharField(max_length=240, blank=True, default="")
    unit = models.CharField("Ед.", max_length=16, default="кг")
    for_task = models.BooleanField("В задание", default=True)
    customer_line = models.ForeignKey(
        OrderLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="supply_lines"
    )
    reserve_kg = models.DecimalField("План резерва, кг", max_digits=12, decimal_places=3, null=True, blank=True)
    caliber = models.ForeignKey("catalog.Caliber", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_lines")
    grade = models.ForeignKey("catalog.Grade", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_lines")
    qty_kg = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        db_table = "purchase_order_lines"
        verbose_name = "Строка заказа поставщику"
        verbose_name_plural = "Строки заказов поставщикам"

    def external_title(self) -> str:
        code = self.external_code
        name = self.external_name
        if self.external_product_id:
            if not code:
                code = self.external_product.code
            if not name:
                name = self.external_product.name
        if code and name:
            return f"{code} · {name}"
        return name or code or ""

    def product_title(self) -> str:
        if self.product_id:
            return self.product.name
        return self.external_title() or "без товара"

    def __str__(self):
        parts = [self.product_title()]
        if self.caliber_id:
            parts.append(self.caliber.name)
        if self.grade_id:
            parts.append(self.grade.name)
        parts.append(f"{format_kg(self.qty_kg)} кг")
        return ", ".join(parts)


class Task(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    purchase_order = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks"
    )
    kind = models.CharField(max_length=16)
    status = models.CharField(max_length=32, default="open")
    supplier = models.ForeignKey("catalog.Supplier", null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    ttn_number = models.CharField(max_length=80, blank=True, default="")
    doc_date = models.DateField(null=True, blank=True)
    receipt = models.ForeignKey("receiving.Receipt", null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    assignee = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
        verbose_name="Сотрудник",
    )
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="tasks_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tasks"
        verbose_name = "Задание"
        verbose_name_plural = "Задания"

    def is_closed(self) -> bool:
        if self.status == "closed":
            return True
        return self.kind == "pick" and self.status == "picked"

    def is_working(self) -> bool:
        if self.kind == "receive" and self.status in ("assigned", "in_work"):
            return True
        if self.kind == "pick":
            return self.status in ("created", "partial", "planned", "in_work")
        return self.status == TASK_WORK_STATUS.get(self.kind)

    def __str__(self):
        return self.number or f"#{self.pk}"


class TaskLine(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(OrderLine, null=True, blank=True, on_delete=models.PROTECT, related_name="task_lines")
    purchase_line = models.ForeignKey(
        PurchaseOrderLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="task_lines"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="task_lines")
    caliber = models.ForeignKey(
        "catalog.Caliber", null=True, blank=True, on_delete=models.PROTECT, related_name="task_lines"
    )
    grade = models.ForeignKey(
        "catalog.Grade", null=True, blank=True, on_delete=models.PROTECT, related_name="task_lines"
    )
    qty_kg = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        db_table = "task_lines"
        verbose_name = "Строка задания"
        verbose_name_plural = "Строки заданий"

    def __str__(self):
        parts = [self.product.name]
        if self.caliber_id:
            parts.append(self.caliber.name)
        if self.grade_id:
            parts.append(self.grade.name)
        parts.append(f"{format_kg(self.qty_kg)} кг")
        return ", ".join(parts)
