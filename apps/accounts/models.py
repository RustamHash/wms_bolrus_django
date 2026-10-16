import hashlib
import secrets

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from apps.common.constants import ROLE_LABELS


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


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField("Имя", max_length=120)
    login = models.CharField("Логин", max_length=80, unique=True)
    pin_hash = models.CharField(max_length=200, blank=True, default="")
    badge_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    role = models.CharField("Роль", max_length=32)
    is_active = models.BooleanField("Активен", default=True)

    USERNAME_FIELD = "login"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    class Meta:
        db_table = "users"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        return self.name

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
