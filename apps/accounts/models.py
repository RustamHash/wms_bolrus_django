import hashlib
import secrets

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from apps.accounts.access import permission_rows
from apps.common.constants import ROLE_LABELS, TSD_ACCESS


class UserManager(BaseUserManager):
    def create_user(self, login, password=None, **extra):
        user = self.model(login=login, **extra)
        user.set_password(password or "")
        user.save(using=self._db)
        return user

    def create_superuser(self, login, password=None, **extra):
        extra.setdefault("role", "admin")
        extra.setdefault("is_active", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("name", login)
        return self.create_user(login, password, **extra)


class Position(models.Model):
    name = models.CharField("Должность", max_length=120, unique=True)
    access_receive = models.BooleanField("Приёмка", default=False)
    access_pick = models.BooleanField("Отбор", default=False)
    access_ship = models.BooleanField("Отгрузка", default=False)
    access_move = models.BooleanField("Перемещение", default=False)
    access_shift = models.BooleanField("Экран смены", default=False)
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        db_table = "positions"
        verbose_name = "Должность"
        verbose_name_plural = "Должности"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def tsd_access(self) -> set[str]:
        return {key for key in TSD_ACCESS if getattr(self, f"access_{key}")}

    def rights_title(self) -> str:
        labels = [TSD_ACCESS[key] for key in TSD_ACCESS if getattr(self, f"access_{key}")]
        if self.access_shift:
            labels.append("Экран смены")
        return ", ".join(labels) or "—"


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField("Имя", max_length=120)
    last_name = models.CharField("Фамилия", max_length=80, blank=True, default="")
    first_name = models.CharField("Имя", max_length=80, blank=True, default="")
    middle_name = models.CharField("Отчество", max_length=80, blank=True, default="")
    login = models.CharField("Логин", max_length=80, unique=True)
    pin_hash = models.CharField(max_length=200, blank=True, default="")
    tsd_code = models.CharField("Короткий код", max_length=8, unique=True, null=True, blank=True)
    badge_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    role = models.CharField("Роль", max_length=32)
    position = models.ForeignKey(
        Position,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="users",
        verbose_name="Должность",
    )
    access_receive = models.BooleanField("Приёмка", default=False)
    access_pick = models.BooleanField("Отбор", default=False)
    access_ship = models.BooleanField("Отгрузка", default=False)
    access_move = models.BooleanField("Перемещение", default=False)
    access_managed = models.BooleanField("Доступы заданы в карточке", default=False)
    is_active = models.BooleanField("Активен", default=True)

    USERNAME_FIELD = "login"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    class Meta:
        db_table = "users"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        permissions = permission_rows()

    def __str__(self):
        return self.name

    def apply_fio(self, last_name: str, first_name: str, middle_name: str = "") -> None:
        self.last_name = last_name
        self.first_name = first_name
        self.middle_name = middle_name
        self.name = " ".join(part for part in (last_name, first_name, middle_name) if part)

    @property
    def active(self):
        return self.is_active

    @active.setter
    def active(self, value):
        self.is_active = value

    @property
    def is_office(self):
        return self.role in ("office", "admin")

    @property
    def is_staff(self):
        return self.opens_admin()

    def can_shift(self) -> bool:
        if not self.is_active:
            return False
        if self.is_superuser or self.role == "admin":
            return True
        if self.access_managed:
            from apps.accounts.access import screen_allows

            return screen_allows(self, "web_shift", "view") or screen_allows(self, "tsd_shift", "view")
        if self.role == "office":
            return True
        position = self.position
        return bool(position and position.is_active and position.access_shift)

    def granted_access(self) -> set[str]:
        if self.role == "admin" or self.is_superuser:
            return set(TSD_ACCESS)
        if self.access_managed:
            from apps.accounts.access import tsd_operation_keys

            return tsd_operation_keys(self)
        if self.role == "employee":
            if self.position_id and self.position.is_active:
                return self.position.tsd_access()
            return {key for key in TSD_ACCESS if getattr(self, f"access_{key}")}
        if self.role in ("office", "admin"):
            return set(TSD_ACCESS)
        if self.role == "receiver":
            return {"receive"}
        if self.role == "warehouse":
            return {"pick", "ship"}
        return set()

    def access_keys(self) -> list[str]:
        granted = self.granted_access()
        return [key for key in TSD_ACCESS if key in granted]

    def access_title(self) -> str:
        keys = self.access_keys()
        if not keys:
            return "—"
        return ", ".join(TSD_ACCESS[key] for key in keys)

    def opens_web(self) -> bool:
        from apps.accounts.access import opens_web

        return opens_web(self)

    def opens_tsd(self) -> bool:
        from apps.accounts.access import opens_tsd

        return opens_tsd(self)

    def opens_admin(self) -> bool:
        if not self.is_active:
            return False
        if self.role == "admin":
            return True
        if AdminRoleGrant.objects.filter(role=self.role).exists():
            return True
        return AdminUserGrant.objects.filter(user_id=self.id).exists()


class AdminRoleGrant(models.Model):
    role = models.CharField("Роль", max_length=32, unique=True)

    class Meta:
        db_table = "admin_role_grants"
        verbose_name = "Допуск роли в админку"
        verbose_name_plural = "Допуски ролей в админку"

    def __str__(self):
        return ROLE_LABELS.get(self.role, self.role)


class AdminUserGrant(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="admin_grant", verbose_name="Пользователь")

    class Meta:
        db_table = "admin_user_grants"
        verbose_name = "Персональный допуск в админку"
        verbose_name_plural = "Персональные допуски в админку"

    def __str__(self):
        return self.user.name


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 120_000)
    return salt.hex() + "$" + digest.hex()


def verify_secret(secret: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, digest_hex = stored.split("$", 1)
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 120_000)
    return secrets.compare_digest(digest.hex(), digest_hex)


def new_badge_token() -> str:
    return "WMS." + secrets.token_urlsafe(16)
