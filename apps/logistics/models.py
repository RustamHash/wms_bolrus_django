from django.db import models


class Vehicle(models.Model):
    plate_number = models.CharField("Госномер", max_length=50)
    brand_model = models.CharField("Марка и модель", max_length=255, blank=True, default="")
    trailer_plate = models.CharField("Госномер прицепа", max_length=50, blank=True, default="")
    load_capacity_kg = models.PositiveIntegerField("Тоннаж, кг", null=True, blank=True)
    pallet_capacity = models.PositiveIntegerField("Кол-во паллет", null=True, blank=True)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        db_table = "logistics_vehicles"
        verbose_name = "Автомобиль"
        verbose_name_plural = "Автомобили"
        ordering = ["plate_number"]

    def __str__(self):
        return self.display_name()

    def display_name(self) -> str:
        parts = [self.plate_number]
        if self.brand_model:
            parts.append(self.brand_model)
        if self.trailer_plate:
            parts.append(f"прицеп {self.trailer_plate}")
        return ", ".join(parts)


class Driver(models.Model):
    full_name = models.CharField("ФИО", max_length=255)
    phone = models.CharField("Телефон", max_length=50, blank=True, default="")
    vehicle = models.ForeignKey(
        Vehicle,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drivers",
        verbose_name="Автомобиль",
    )
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        db_table = "logistics_drivers"
        verbose_name = "Водитель"
        verbose_name_plural = "Водители"
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name

    def vehicle_display(self) -> str:
        return self.vehicle.display_name() if self.vehicle_id else ""


class Route(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PLANNED = "planned", "Запланирован"
        DOCKED = "docked", "Поставлено к отгрузке"
        COMPLETED = "completed", "Отгружен"
        CANCELLED = "cancelled", "Отменён"

    LIVE_STATUSES = (Status.DRAFT, Status.PLANNED)
    ASSIGNED_STATUSES = (Status.DRAFT, Status.PLANNED, Status.DOCKED, Status.COMPLETED)

    route_date = models.DateField("Дата маршрута", db_index=True)
    name = models.CharField("Название", max_length=255, blank=True, default="")
    driver = models.ForeignKey(
        Driver,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="routes",
        verbose_name="Водитель",
    )
    vehicle_text = models.CharField("Автомобиль", max_length=255, blank=True, default="")
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    planned_load_at = models.DateTimeField("План погрузки", null=True, blank=True)
    arrived_at = models.DateTimeField("Прибыл", null=True, blank=True)
    docked_at = models.DateTimeField("Поставлено к отгрузке", null=True, blank=True)
    actual_load_at = models.DateTimeField("Факт отгрузки", null=True, blank=True)
    departed_at = models.DateTimeField("Убыл", null=True, blank=True)
    notes = models.TextField("Комментарий", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "logistics_routes"
        verbose_name = "Маршрут"
        verbose_name_plural = "Маршруты"
        ordering = ["-route_date", "-id"]

    def __str__(self):
        label = self.name or f"Маршрут {self.pk or ''}"
        return f"{label} ({self.route_date:%d.%m.%Y})"

    def refresh_vehicle_text(self) -> None:
        if not self.driver_id:
            self.vehicle_text = ""
            return
        driver = (
            self.driver
            if getattr(self, "driver", None) is not None and self.driver.pk == self.driver_id
            else Driver.objects.select_related("vehicle").filter(pk=self.driver_id).first()
        )
        if driver and driver.vehicle_id:
            self.vehicle_text = driver.vehicle.display_name()
        else:
            self.vehicle_text = ""

    def save(self, *args, **kwargs):
        old_status = None
        if self.pk and not self._state.adding:
            old_status = Route.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        frozen = old_status is not None and old_status not in self.LIVE_STATUSES
        if self.status in self.LIVE_STATUSES or not frozen or not self.vehicle_text:
            self.refresh_vehicle_text()
        super().save(*args, **kwargs)


class RouteOrder(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="route_orders", verbose_name="Маршрут")
    order = models.ForeignKey(
        "planning.Order",
        on_delete=models.PROTECT,
        related_name="route_orders",
        verbose_name="Заказ",
    )
    position = models.PositiveIntegerField("Порядок", default=1)

    class Meta:
        db_table = "logistics_route_orders"
        verbose_name = "Заказ в маршруте"
        verbose_name_plural = "Заказы в маршруте"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["route", "order"], name="uniq_logistics_route_order"),
        ]

    def __str__(self):
        return f"{self.route_id} → {self.order_id} ({self.position})"
