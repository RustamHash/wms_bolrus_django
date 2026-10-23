import io

import qrcode
from django.contrib import messages
from django.contrib.auth import login, logout
from django.db.models import F, ProtectedError, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.access import (
    ACTIONS,
    TSD_SCREENS,
    WEB_SCREENS,
    entry_label,
    opens_tsd,
    opens_web,
    read_grants,
    role_presets,
    screen_rows,
    sync_user_screens,
)
from apps.accounts.models import Position, User, new_badge_token
from apps.accounts.services import (
    assign_position,
    assignable_roles,
    can_open_admin,
    ensure_keeper,
    position_choices,
    sync_user_grant,
    user_has_grant,
)
from apps.catalog.services import NAV
from apps.common.constants import TASK_WORK_STATUS
from apps.common.exceptions import WarehouseError
from apps.common.forms import optional_int, text
from apps.common.http import done, guarded, roles_required
from apps.common.notices import mark_notices_seen
from apps.common.kg import as_kg
from apps.inventory.models import Balance
from apps.planning.models import Order, Task


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
        return None, "Введите код или отсканируйте бейдж"
    token = raw[raw.rfind("WMS.") :] if "WMS." in raw else ""
    token = token.split()[0].strip() if token else ""
    if token.startswith("WMS."):
        user = User.objects.filter(badge_token=token, is_active=True).first()
        if user is None:
            return None, "Бейдж не распознан"
        return user, None
    by_code = list(User.objects.filter(tsd_code=raw, is_active=True)[:2])
    if len(by_code) > 1:
        return None, "Этот код задан нескольким сотрудникам"
    if len(by_code) == 1:
        return by_code[0], None
    return None, "Неверный код"


def login_form(request):
    return render(request, "login.html")


def login_submit(request):
    login_name = text(request.POST, "login")
    password = request.POST.get("password") or ""
    user = User.objects.filter(login=login_name, is_active=True).first()
    if user is None or not user.check_password(password):
        messages.error(request, "Неверный логин или пароль")
        return redirect("/login")
    if not opens_web(user):
        if opens_tsd(user):
            messages.error(request, "Этот пользователь входит только на ТСД")
        else:
            messages.error(request, "У пользователя нет доступа в веб и на ТСД")
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
        messages.error(request, error or "Неверный код")
        return redirect("/tsd/login")
    if not opens_tsd(user):
        messages.error(request, "У пользователя нет доступа на ТСД")
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
        {
            "title": "Сервис",
            "items": [
                {"href": "/tsd", "title": "ТСД"},
                {"href": "/admin", "title": "Админка"},
                {"href": "/service/imports", "title": "Импорт Excel (запасной)"},
            ],
        },
    )


@roles_required("office")
def service_imports(request):
    return render(request, "service/imports.html")


def home(request):
    if not request.user.is_authenticated:
        return redirect("/login")
    balances = Balance.objects.aggregate(total=Sum(F("free_kg") + F("reserved_kg") + F("picked_kg")))
    return render(
        request,
        "home.html",
        {
            "orders": Order.objects.filter(status="open").count(),
            "receive": Task.objects.filter(kind="receive", status__in=(TASK_WORK_STATUS["receive"], "assigned")).count(),
            "pick": Task.objects.filter(kind="pick", status__in=("created", "partial", "planned")).count(),
            "ship": Task.objects.filter(kind="ship", status=TASK_WORK_STATUS["ship"]).count(),
            "stock_kg": as_kg(balances["total"] or 0),
        },
    )


@roles_required("office")
def users_list(request):
    users = list(
        User.objects.select_related("position").prefetch_related("user_permissions").order_by("last_name", "first_name", "name")
    )
    _issue_missing_badges(users)
    for item in users:
        item.entry_label = entry_label(item)
    return render(request, "users/list.html", {"users": users, "nav": NAV, "kind": "users"})


def _user_form(editor, item):
    return {
        "roles": assignable_roles(editor),
        "positions": position_choices(item.position_id if item else None),
        "can_grant": can_open_admin(editor),
        "admin_access": bool(item and user_has_grant(item.id)),
        "nav": NAV,
        "kind": "users",
        "actions": ACTIONS,
        "web_screens": screen_rows(item, WEB_SCREENS),
        "tsd_screens": screen_rows(item, TSD_SCREENS),
        "role_presets": role_presets(),
    }


@roles_required("office")
def users_new(request):
    return render(request, "users/form.html", {"item": None, **_user_form(request.user, None)})


def _person_fio(form):
    last_name = text(form, "last_name")
    first_name = text(form, "first_name")
    middle_name = text(form, "middle_name")
    if not last_name or not first_name:
        raise WarehouseError("Укажите фамилию и имя")
    full = " ".join(part for part in (last_name, first_name, middle_name) if part)
    if len(full) > 120:
        raise WarehouseError("ФИО длиннее 120 символов")
    return last_name, first_name, middle_name


def _person_code(form, user_id: int | None) -> str | None:
    code = text(form, "tsd_code")
    if not code:
        return None
    if not code.isdigit() or not 4 <= len(code) <= 6:
        raise WarehouseError("Короткий код — от 4 до 6 цифр")
    if User.objects.filter(tsd_code=code).exclude(pk=user_id).exists():
        raise WarehouseError("Этот код уже используется")
    return code


@roles_required("office")
def users_create(request):
    form = request.POST

    def action():
        last_name, first_name, middle_name = _person_fio(form)
        login_name = text(form, "login")
        password = form.get("password") or ""
        role = text(form, "role") or "none"
        if not login_name:
            raise WarehouseError("Укажите логин")
        if role not in assignable_roles(request.user):
            raise WarehouseError("Выберите роль")
        item = User(
            login=login_name,
            tsd_code=_person_code(form, None),
            badge_token=new_badge_token(),
            role=role,
            is_active=form.get("active") == "1",
        )
        item.apply_fio(last_name, first_name, middle_name)
        assign_position(item, optional_int(form, "position_id"), required=False)
        if password:
            item.set_password(password)
        else:
            item.set_unusable_password()
        item.save()
        if can_open_admin(request.user):
            sync_user_grant(item, form.get("admin_access") == "1")
        if form.get("screens_present") == "1":
            sync_user_screens(item, read_grants(form))
        return item

    result, response = guarded(request, "/users/new", action)
    if response:
        return response
    return done(request, "/users", "Пользователь создан")


@roles_required("office")
def users_edit(request, user_id):
    item = get_object_or_404(User.objects.prefetch_related("user_permissions"), pk=user_id)
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
        last_name, first_name, middle_name = _person_fio(form)
        login_name = text(form, "login")
        role = text(form, "role") or "none"
        if not login_name:
            raise WarehouseError("Укажите логин")
        if item.role == "admin" and not can_open_admin(request.user):
            raise WarehouseError("Этого пользователя меняет администратор")
        if role not in assignable_roles(request.user):
            raise WarehouseError("Выберите роль")
        item.apply_fio(last_name, first_name, middle_name)
        item.login = login_name
        item.role = role
        item.tsd_code = _person_code(form, item.id)
        assign_position(item, optional_int(form, "position_id"), required=False)
        item.is_active = form.get("active") == "1"
        password = form.get("password") or ""
        if password:
            item.set_password(password)
        if not item.badge_token:
            item.badge_token = new_badge_token()
        ensure_keeper(item)
        item.save()
        if can_open_admin(request.user):
            sync_user_grant(item, form.get("admin_access") == "1")
        if form.get("screens_present") == "1":
            sync_user_screens(item, read_grants(form))
        return item

    result, response = guarded(request, f"/users/{user_id}", action)
    if response:
        return response
    return done(request, "/users", "Сохранено")


@roles_required("office")
def users_delete(request, user_id):
    def action():
        item = User.objects.filter(pk=user_id).first()
        if item is None:
            raise WarehouseError("Пользователь не найден")
        if item.id == request.user.id:
            raise WarehouseError("Свою карточку удалить нельзя")
        if item.role == "admin" and not can_open_admin(request.user):
            raise WarehouseError("Этого пользователя удаляет администратор")
        name = item.name
        try:
            item.delete()
        except ProtectedError as exc:
            raise WarehouseError(
                "Пользователь уже есть в документах склада, его нельзя удалить. Снимите галочку «Активен»."
            ) from exc
        return name

    result, response = guarded(request, "/users", action)
    if response:
        return response
    return done(request, "/users", f"Пользователь {result} удалён")


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
    return done(request, f"/users/{result.id}", "Новый QR-код выпущен, старый бейдж больше не действует")


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


def staff_list(request):
    return redirect("/users")


def staff_new(request):
    return redirect("/users/new")


def staff_create(request):
    return redirect("/users/new")


def staff_edit(request, user_id):
    return redirect(f"/users/{user_id}")


def staff_update(request, user_id):
    return redirect(f"/users/{user_id}")


_POSITION_RIGHTS = (
    ("receive", "access_receive", "Приёмка"),
    ("pick", "access_pick", "Отбор"),
    ("ship", "access_ship", "Отгрузка"),
    ("move", "access_move", "Перемещение"),
    ("shift", "access_shift", "Экран смены"),
)


def _position_form(item):
    selected = set()
    if item is not None:
        selected = {key for key, field, _label in _POSITION_RIGHTS if getattr(item, field)}
    return {
        "rights": _POSITION_RIGHTS,
        "selected": selected,
        "nav": NAV,
        "kind": "positions",
    }


def _apply_position_rights(item: Position, form) -> None:
    selected = set(form.getlist("access"))
    known = {key for key, _field, _label in _POSITION_RIGHTS}
    if not selected <= known:
        raise WarehouseError("Некорректное право")
    for key, field, _label in _POSITION_RIGHTS:
        setattr(item, field, key in selected)


@roles_required("office")
def positions_list(request):
    rows = list(Position.objects.order_by("name"))
    return render(request, "positions/list.html", {"rows": rows, "nav": NAV, "kind": "positions"})


@roles_required("office")
def positions_new(request):
    return render(request, "positions/form.html", {"item": None, **_position_form(None)})


@roles_required("office")
def positions_create(request):
    form = request.POST

    def action():
        name = text(form, "name")
        if not name:
            raise WarehouseError("Укажите название должности")
        item = Position(name=name, is_active=form.get("active") == "1")
        _apply_position_rights(item, form)
        item.save()
        return item

    result, response = guarded(request, "/positions/new", action)
    if response:
        return response
    return done(request, "/positions", "Должность создана")


@roles_required("office")
def positions_edit(request, item_id):
    item = get_object_or_404(Position, pk=item_id)
    return render(request, "positions/form.html", {"item": item, **_position_form(item)})


@roles_required("office")
def positions_update(request, item_id):
    form = request.POST

    def action():
        item = Position.objects.filter(pk=item_id).first()
        if item is None:
            raise WarehouseError("Должность не найдена")
        name = text(form, "name")
        if not name:
            raise WarehouseError("Укажите название должности")
        item.name = name
        item.is_active = form.get("active") == "1"
        _apply_position_rights(item, form)
        item.save()
        return item

    result, response = guarded(request, f"/positions/{item_id}", action)
    if response:
        return response
    return done(request, "/positions", "Сохранено")


@roles_required("office")
def positions_delete(request, item_id):
    def action():
        item = Position.objects.filter(pk=item_id).first()
        if item is None:
            raise WarehouseError("Должность не найдена")
        item.delete()
        return item

    _result, response = guarded(request, "/positions", action)
    if response:
        return response
    return done(request, "/positions", "Должность удалена")
