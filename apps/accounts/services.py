from django.db.models import Q

from apps.accounts.models import AdminRoleGrant, AdminUserGrant, Position, User
from apps.common.constants import ROLE_LABELS
from apps.common.exceptions import WarehouseError

KEEPER_ROLES = ("office", "admin")
GRANTABLE_ROLES = ("receiver", "warehouse", "office")


def can_open_admin(user) -> bool:
    return bool(user and user.is_active and user.opens_admin())


def assignable_roles(editor) -> dict[str, str]:
    roles = dict(ROLE_LABELS)
    roles.pop("employee", None)
    if not can_open_admin(editor):
        roles.pop("admin", None)
    return roles


def position_choices(current_id: int | None = None):
    query = Q(is_active=True)
    if current_id:
        query |= Q(pk=current_id)
    return Position.objects.filter(query).order_by("name")


def assign_position(item: User, position_id: int | None, *, required: bool) -> None:
    if not position_id:
        if required:
            raise WarehouseError("Выберите должность")
        item.position = None
        return
    position = Position.objects.filter(pk=position_id).first()
    if position is None or (not position.is_active and position.id != item.position_id):
        raise WarehouseError("Должность не найдена")
    item.position = position


def user_has_grant(user_id: int) -> bool:
    return AdminUserGrant.objects.filter(user_id=user_id).exists()


def sync_user_grant(item: User, enabled: bool) -> None:
    existing = AdminUserGrant.objects.filter(user_id=item.id).first()
    if enabled and existing is None:
        AdminUserGrant.objects.create(user=item)
    elif not enabled and existing is not None:
        existing.delete()


def ensure_keeper(item: User) -> None:
    if item.role in KEEPER_ROLES and item.is_active:
        return
    others = User.objects.filter(role__in=KEEPER_ROLES, is_active=True).exclude(pk=item.pk).exists()
    if not others:
        raise WarehouseError("Должен остаться хотя бы один активный пользователь офиса или администратор")
