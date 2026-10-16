from apps.accounts.models import AdminRoleGrant, AdminUserGrant, User
from apps.common.constants import ROLE_LABELS
from apps.common.exceptions import WarehouseError

KEEPER_ROLES = ("office", "admin")
GRANTABLE_ROLES = ("receiver", "warehouse", "office")


def can_open_admin(user) -> bool:
    return bool(user and user.is_active and user.opens_admin())


def assignable_roles(editor) -> dict[str, str]:
    roles = dict(ROLE_LABELS)
    if not can_open_admin(editor):
        roles.pop("admin", None)
    return roles


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
