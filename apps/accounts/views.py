import io

import qrcode
from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import F, Sum

from apps.accounts.models import User, hash_secret, new_badge_token, verify_secret
from apps.accounts.services import assignable_roles, can_open_admin, ensure_keeper, sync_user_grant, user_has_grant
from apps.catalog.services import NAV
from apps.common.constants import TASK_WORK_STATUS
from apps.common.exceptions import WarehouseError
from apps.common.forms import text
from apps.common.http import done, guarded, roles_required
from apps.common.notices import mark_notices_seen
from apps.common.kg import as_kg
from apps.inventory.models import Balance
from apps.planning.models import Order, Task


def _pin_taken(pin: str, user_id: int | None) -> bool:
    for user in User.objects.exclude(pin_hash=""):
        if user.id != user_id and verify_secret(pin, user.pin_hash):
            return True
    return False


def _issue_missing_badges(users) -> None:
    changed = False
    for item in users:
        if not item.badge_token:
            item.badge_token = new_badge_token()
            item.save(update_fields=["badge_token"])
            changed = True
    return changed


def find_user_for_tsd(code: str):
    raw = code.strip()
    if not raw:
        return None, "Введите PIN или отсканируйте бейдж"
    token = raw[raw.rfind("WMS.") :] if "WMS." in raw else ""
    token = token.split()[0].strip() if token else ""
    if token.startswith("WMS."):
        user = User.objects.filter(badge_token=token, is_active=True).first()
        if user is None:
            return None, "Бейдж не распознан"
        return user, None
    matches = [user for user in User.objects.filter(is_active=True).exclude(pin_hash="") if verify_secret(raw, user.pin_hash)]
    if not matches:
        return None, "Неверный PIN"
    if len(matches) > 1:
        return None, "Этот PIN задан нескольким пользователям"
    return matches[0], None


def login_form(request):
    return render(request, "login.html")


def login_submit(request):
    login_name = text(request.POST, "login")
    password = request.POST.get("password") or ""
    user = User.objects.filter(login=login_name, is_active=True).first()
    if user is None or not user.check_password(password):
        messages.error(request, "Неверный логин или пароль")
        return redirect("/login")
    login(request, user)
    return redirect("/")


def logout_submit(request):
    target = "/tsd/login" if text(request.POST, "next") == "/tsd/login" else "/login"
    logout(request)
    return redirect(target)


def notices_seen(request):
    if request.user.is_authenticated:
        mark_notices_seen(request.user)
    return HttpResponse(status=204)


def tsd_login_form(request):
    return render(request, "tsd_login.html")


def tsd_login_submit(request):
    user, error = find_user_for_tsd(text(request.POST, "code"))
    if user is None:
        messages.error(request, error or "Неверный PIN")
        return redirect("/tsd/login")
    login(request, user)
    return redirect("/tsd")


def service_home(request):
    user = request.user
    if not user.is_authenticated:
        return redirect("/login")
    if not user.opens_admin():
        messages.error(request, "Недостаточно прав")
        return redirect("/")
    return render(
        request,
        "hub.html",
        {"title": "Сервис", "items": [{"href": "/tsd", "title": "ТСД"}, {"href": "/admin", "title": "Админка"}]},
    )


def home(request):
    if not request.user.is_authenticated:
        return redirect("/login")
    balances = Balance.objects.aggregate(total=Sum(F("free_kg") + F("reserved_kg") + F("picked_kg")))
    return render(
        request,
        "home.html",
        {
            "orders": Order.objects.filter(status="open").count(),
            "receive": Task.objects.filter(kind="receive", status=TASK_WORK_STATUS["receive"]).count(),
            "pick": Task.objects.filter(kind="pick", status=TASK_WORK_STATUS["pick"]).count(),
            "ship": Task.objects.filter(kind="ship", status=TASK_WORK_STATUS["ship"]).count(),
            "stock_kg": as_kg(balances["total"] or 0),
        },
    )


@roles_required("office")
def users_list(request):
    users = list(User.objects.order_by("name"))
    _issue_missing_badges(users)
    return render(request, "users/list.html", {"users": users, "nav": NAV, "kind": "users"})


def _user_form(editor, item):
    return {
        "roles": assignable_roles(editor),
        "can_grant": can_open_admin(editor),
        "admin_access": bool(item and user_has_grant(item.id)),
        "nav": NAV,
        "kind": "users",
    }


@roles_required("office")
def users_new(request):
    return render(request, "users/form.html", {"item": None, **_user_form(request.user, None)})


@roles_required("office")
def users_create(request):
    form = request.POST

    def action():
        name = text(form, "name")
        login_name = text(form, "login")
        password = form.get("password") or ""
        pin = text(form, "pin")
        role = text(form, "role")
        if not name or not login_name or not password:
            raise WarehouseError("Укажите имя, логин и пароль")
        if role not in assignable_roles(request.user):
            raise WarehouseError("Выберите роль")
        if pin and _pin_taken(pin, None):
            raise WarehouseError("Этот PIN уже используется")
        item = User(
            name=name,
            login=login_name,
            pin_hash=hash_secret(pin) if pin else "",
            badge_token=new_badge_token(),
            role=role,
            is_active=form.get("active") == "1",
        )
        item.set_password(password)
        item.save()
        if can_open_admin(request.user):
            sync_user_grant(item, form.get("admin_access") == "1")
        return item

    result, response = guarded(request, "/users/new", action)
    if response:
        return response
    return done(request, "/users", "Пользователь создан")


@roles_required("office")
def users_edit(request, user_id):
    item = get_object_or_404(User, pk=user_id)
    if item.role == "admin" and not can_open_admin(request.user):
        messages.error(request, "Недостаточно прав")
        return redirect("/")
    _issue_missing_badges([item])
    return render(request, "users/form.html", {"item": item, **_user_form(request.user, item)})


@roles_required("office")
def users_update(request, user_id):
    form = request.POST

    def action():
        item = User.objects.filter(pk=user_id).first()
        if item is None:
            raise WarehouseError("Пользователь не найден")
        name = text(form, "name")
        login_name = text(form, "login")
        role = text(form, "role")
        if not name or not login_name:
            raise WarehouseError("Укажите имя и логин")
        if item.role == "admin" and not can_open_admin(request.user):
            raise WarehouseError("Этого пользователя меняет администратор")
        if role not in assignable_roles(request.user):
            raise WarehouseError("Выберите роль")
        pin = text(form, "pin")
        if pin and _pin_taken(pin, item.id):
            raise WarehouseError("Этот PIN уже используется")
        item.name = name
        item.login = login_name
        item.role = role
        item.is_active = form.get("active") == "1"
        password = form.get("password") or ""
        if password:
            item.set_password(password)
        if pin:
            item.pin_hash = hash_secret(pin)
        ensure_keeper(item)
        item.save()
        if can_open_admin(request.user):
            sync_user_grant(item, form.get("admin_access") == "1")
        return item

    result, response = guarded(request, f"/users/{user_id}", action)
    if response:
        return response
    return done(request, "/users", "Сохранено")


@roles_required("office")
def users_new_badge(request, user_id):
    def action():
        item = User.objects.filter(pk=user_id).first()
        if item is None:
            raise WarehouseError("Пользователь не найден")
        item.badge_token = new_badge_token()
        item.save(update_fields=["badge_token"])
        return item

    result, response = guarded(request, f"/users/{user_id}", action)
    if response:
        return response
    return done(request, f"/users/{user_id}", "Новый QR-код выпущен, старый бейдж больше не действует")


@roles_required("office")
def users_badge_png(request, user_id):
    item = get_object_or_404(User, pk=user_id)
    _issue_missing_badges([item])
    image = qrcode.make(item.badge_token)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png", headers={"Cache-Control": "no-store"})


@roles_required("office")
def users_badge_page(request, user_id):
    item = get_object_or_404(User, pk=user_id)
    _issue_missing_badges([item])
    return render(request, "users/badge.html", {"item": item})
