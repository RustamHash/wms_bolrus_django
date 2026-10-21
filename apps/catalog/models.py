from django.db import models


class ProductGroup(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)

    class Meta:
        db_table = "product_groups"
        verbose_name = "Группа товаров"
        verbose_name_plural = "Группы товаров"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    code = models.CharField("Код", max_length=32, unique=True)
    barcode = models.CharField("Штрихкод", max_length=64, unique=True, null=True, blank=True)
    group = models.ForeignKey(
        ProductGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="products", verbose_name="Группа"
    )

    class Meta:
        db_table = "products"
        verbose_name = "Номенклатура"
        verbose_name_plural = "Номенклатура"
        ordering = ["code"]

    def __str__(self):
        return self.name


class ExternalProduct(models.Model):
    name = models.CharField("Название", max_length=120)
    code = models.CharField("Код", max_length=32, unique=True)
    group = models.ForeignKey(
        ProductGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="external_products",
        verbose_name="Группа",
    )

    class Meta:
        db_table = "external_products"
        verbose_name = "Номенклатура 1С"
        verbose_name_plural = "Номенклатура 1С"
        ordering = ["code"]

    def __str__(self):
        return self.name


class ProductAlias(models.Model):
    external_product = models.OneToOneField(
        ExternalProduct, on_delete=models.CASCADE, related_name="alias", verbose_name="Номенклатура 1С"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="aliases", verbose_name="Номенклатура")

    class Meta:
        db_table = "product_aliases"
        verbose_name = "Соответствие номенклатуры"
        verbose_name_plural = "Соответствия номенклатуры"

    def __str__(self):
        return self.external_product.code


class Caliber(models.Model):
    name = models.CharField("Калибр", max_length=80, unique=True)
    sort_order = models.IntegerField("Порядок", default=0)

    class Meta:
        db_table = "calibers"
        verbose_name = "Калибр"
        verbose_name_plural = "Калибры"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Grade(models.Model):
    name = models.CharField("Сортность", max_length=80, unique=True)
    sort_order = models.IntegerField("Порядок", default=0)

    class Meta:
        db_table = "grades"
        verbose_name = "Сортность"
        verbose_name_plural = "Сортность"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Packaging(models.Model):
    name = models.CharField("Название", max_length=80, unique=True)
    tare_kg = models.DecimalField("Вес тары, кг", max_digits=12, decimal_places=3, default=0)

    class Meta:
        db_table = "packagings"
        verbose_name = "Упаковка"
        verbose_name_plural = "Упаковка"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField("Поставщик", max_length=200, unique=True)
    code = models.CharField("Код", max_length=32, unique=True, null=True, blank=True)

    class Meta:
        db_table = "suppliers"
        verbose_name = "Поставщик"
        verbose_name_plural = "Поставщики"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Client(models.Model):
    name = models.CharField("Покупатель", max_length=200, unique=True)
    code = models.CharField("Код", max_length=32, unique=True, null=True, blank=True)

    class Meta:
        db_table = "clients"
        verbose_name = "Покупатель"
        verbose_name_plural = "Покупатели"
        ordering = ["name"]

    def __str__(self):
        return self.name


class DeliveryZone(models.Model):
    name = models.CharField("Зона доставки", max_length=120, unique=True)

    class Meta:
        db_table = "delivery_zones"
        verbose_name = "Зона доставки"
        verbose_name_plural = "Зоны доставки"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ConsigneeCategory(models.Model):
    name = models.CharField("Категория", max_length=80, unique=True)
    sort_order = models.IntegerField("Порядок", default=0)
    grade = models.ForeignKey(
        Grade,
        on_delete=models.PROTECT,
        related_name="consignee_categories",
        verbose_name="Сортность",
    )

    class Meta:
        db_table = "consignee_categories"
        verbose_name = "Категория грузополучателя"
        verbose_name_plural = "Категории грузополучателей"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Consignee(models.Model):
    name = models.CharField("Грузополучатель", max_length=200)
    code = models.CharField("Код", max_length=32, unique=True, null=True, blank=True)
    address = models.TextField("Адрес", blank=True, default="")
    shipping_terms = models.TextField("Условия отгрузки", blank=True, default="")
    zone = models.ForeignKey(
        DeliveryZone,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consignees",
        verbose_name="Зона доставки",
    )
    delivery_order = models.PositiveIntegerField(
        "Очередность в зоне",
        default=0,
        help_text="Порядок остановки при сборке маршрута. 0 — без очереди, точка встаёт в конец.",
    )
    category = models.ForeignKey(
        ConsigneeCategory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consignees",
        verbose_name="Категория",
    )
    allowed_grades = models.ManyToManyField(
        Grade,
        blank=True,
        related_name="consignees",
        verbose_name="Допустимая сортность",
    )

    class Meta:
        db_table = "consignees"
        verbose_name = "Грузополучатель"
        verbose_name_plural = "Грузополучатели"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Seller(models.Model):
    code = models.CharField("Код", max_length=32, unique=True)
    name = models.CharField("Название", max_length=200, unique=True)
    requisites = models.TextField("Реквизиты", blank=True, default="")

    class Meta:
        db_table = "sellers"
        verbose_name = "Продавец"
        verbose_name_plural = "Продавцы"
        ordering = ["name"]

    def __str__(self):
        return self.name
