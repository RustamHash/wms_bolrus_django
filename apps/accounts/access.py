"""Доступы к меню веба и ТСД. Галочки — права Django (чтение, создание, запись, удаление)."""

from django.db.models import Q

ACTIONS = (
    ("view", "Чтение"),
    ("add", "Создание"),
    ("change", "Запись"),
    ("delete", "Удаление"),
)

WEB_PUBLIC = ("web_orders", "web_tasks", "web_reports", "web_shipments")
WEB_OFFICE = ("web_logistics", "web_masters")
TSD_OPS = ("receive", "pick", "ship", "move")


def _models(app, *names):
    return tuple((app, name) for name in names)


WEB_SCREENS = (
    {
        "key": "web_orders",
        "title": "Заказы",
        "hint": "Грузополучатели и поставщики",
        "models": _models(
            "planning",
            "order",
            "orderline",
            "orderlinegrade",
            "orderrevision",
            "purchaseorder",
            "purchaseorderline",
            "purchaseorderrevision",
        ),
    },
    {
        "key": "web_tasks",
        "title": "Задания",
        "hint": "Приёмка, отбор, отгрузка",
        "models": _models("planning", "task", "taskline"),
    },
    {
        "key": "web_reports",
        "title": "Отчеты",
        "hint": "Остатки, потребность, паллеты, инвентаризация, переработка",
        "models": _models(
            "inventory",
            "balance",
            "movement",
            "inventorydocument",
            "inventoryline",
            "transformdocument",
            "transforminput",
            "transformoutput",
            "transformwaste",
        )
        + _models("storage", "pallet", "handlingunit"),
    },
    {
        "key": "web_shipments",
        "title": "Журналы",
        "hint": "Приходы и движения",
        "models": _models("shipping", "allocation") + _models("receiving", "receipt", "lot"),
    },
    {
        "key": "web_shift",
        "title": "Смена",
        "models": _models("logistics", "route", "routeorder"),
    },
    {
        "key": "web_logistics",
        "title": "Логистика",
        "hint": "Маршруты, водители, автомобили",
        "models": _models("logistics", "route", "routeorder", "driver", "vehicle"),
    },
    {
        "key": "web_masters",
        "title": "Справочники",
        "models": _models(
            "catalog",
            "productgroup",
            "product",
            "externalproduct",
            "productalias",
            "caliber",
            "grade",
            "packaging",
            "supplier",
            "deliveryzone",
            "consigneecategory",
            "consignee",
            "seller",
        )
        + _models("accounts", "user", "position")
        + _models("printing", "printer")
        + _models("storage", "warehousezone", "warehousecell"),
    },
    {
        "key": "web_service",
        "title": "Сервис",
        "hint": "Импорт и допуски админки",
        "models": _models("accounts", "adminrolegrant", "adminusergrant"),
    },
)

TSD_SCREENS = (
    {
        "key": "tsd_receive",
        "title": "Приёмка",
        "models": _models("planning", "task", "taskline") + _models("receiving", "receipt", "lot"),
    },
    {
        "key": "tsd_pick",
        "title": "Отбор",
        "models": _models("planning", "task", "taskline"),
    },
    {
        "key": "tsd_ship",
        "title": "На отгрузку",
        "models": _models("planning", "task", "taskline"),
    },
    {
        "key": "tsd_move",
        "title": "Перемещение",
        "models": _models("storage", "pallet", "handlingunit"),
    },
    {
        "key": "tsd_find",
        "title": "Поиск по штрихкоду",
        "models": _models("storage", "handlingunit", "pallet"),
    },
    {
        "key": "tsd_inventory",
        "title": "Инвентаризация",
        "models": _models("inventory", "inventorydocument", "inventoryline"),
    },
    {
        "key": "tsd_shift",
        "title": "Смена",
        "models": _models("logistics", "route", "routeorder"),
    },
)

ALL_SCREENS = WEB_SCREENS + TSD_SCREENS
_BY_KEY = {screen["key"]: screen for screen in ALL_SCREENS}


def permission_rows():
    rows = []
    for screen in ALL_SCREENS:
        for action, label in ACTIONS:
            rows.append((f"screen_{screen['key']}_{action}", f"{screen['title']} — {label}"))
    return rows


def screen_codename(screen, action) -> str:
    return f"screen_{screen}_{action}"


def screen_perm(screen, action) -> str:
    return f"accounts.{screen_codename(screen, action)}"


def legacy_visible(user, screen: str) -> bool:
    if not getattr(user, "is_active", False):
        return False
    if screen == "web_service":
        return user.opens_admin()
    if screen == "web_shift":
        return user.can_shift()
    if screen in WEB_OFFICE:
        return user.is_office
    if screen in WEB_PUBLIC:
        return user.role in ("receiver", "warehouse", "office", "admin")
    if screen == "tsd_find":
        return user.role in ("receiver", "warehouse", "office", "admin")
    if screen == "tsd_inventory":
        return user.role in ("receiver", "warehouse", "office", "admin")
    if screen == "tsd_shift":
        return user.can_shift()
    if screen.startswith("tsd_"):
        return screen.removeprefix("tsd_") in user.granted_access()
    return False


def screen_checked(user, screen: str, action: str) -> bool:
    """Что отмечено в карточке, без обхода администратора."""
    if user is None or not getattr(user, "is_active", False):
        return False
    if user.access_managed and user.pk:
        return user.has_perm(screen_perm(screen, action))
    return legacy_visible(user, screen)


def opens_web(user) -> bool:
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "role", "") == "admin":
        return True
    return any(screen_allows(user, screen["key"], "view") for screen in WEB_SCREENS)


def opens_tsd(user) -> bool:
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "role", "") == "admin":
        return True
    return any(screen_allows(user, screen["key"], "view") for screen in TSD_SCREENS)


def entry_label(user) -> str:
    web = opens_web(user)
    tsd = opens_tsd(user)
    if web and tsd:
        return "Веб и ТСД"
    if web:
        return "Веб"
    if tsd:
        return "ТСД"
    return "без входа"


def screen_allows(user, screen: str, action: str = "view") -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser or user.role == "admin":
        return True
    if not user.access_managed:
        return legacy_visible(user, screen)
    return user.has_perm(screen_perm(screen, action))


def tsd_operation_keys(user) -> set[str]:
    return {
        key
        for key in TSD_OPS
        if user.has_perm(screen_perm(f"tsd_{key}", "view")) or user.has_perm(screen_perm(f"tsd_{key}", "change"))
    }


def screen_rows(user, screens) -> list[dict]:
    rows = []
    for screen in screens:
        rows.append(
            {
                "key": screen["key"],
                "title": screen["title"],
                "hint": screen.get("hint", ""),
                "checks": {action: screen_checked(user, screen["key"], action) for action, _label in ACTIONS},
            }
        )
    return rows


def role_presets() -> dict[str, list[str]]:
    from apps.accounts.models import User

    presets = {}
    for role in ("none", "receiver", "warehouse", "office", "admin"):
        stub = User(role=role, is_active=True, access_managed=False)
        marks = []
        for screen in ALL_SCREENS:
            if legacy_visible(stub, screen["key"]):
                marks.extend(f"{screen['key']}:{action}" for action, _label in ACTIONS)
        presets[role] = marks
    return presets


def read_grants(form) -> set[tuple[str, str]]:
    valid = {(screen["key"], action) for screen in ALL_SCREENS for action, _label in ACTIONS}
    grants = set()
    for raw in form.getlist("screen"):
        key, sep, action = raw.partition(":")
        if sep and (key, action) in valid:
            grants.add((key, action))
    return grants


def _catalog_permissions():
    from django.contrib.auth.models import Permission

    screen_names = [screen_codename(screen["key"], action) for screen in ALL_SCREENS for action, _label in ACTIONS]
    query = Q(content_type__app_label="accounts", content_type__model="user", codename__in=screen_names)
    seen = set()
    for screen in ALL_SCREENS:
        for app, model in screen["models"]:
            pair = (app, model)
            if pair in seen:
                continue
            seen.add(pair)
            names = [f"{action}_{model}" for action, _label in ACTIONS]
            query |= Q(content_type__app_label=app, content_type__model=model, codename__in=names)
    return Permission.objects.filter(query)


def sync_user_screens(user, grants: set[tuple[str, str]]) -> None:
    from django.contrib.auth.models import Permission

    catalog = list(_catalog_permissions())
    by_natural = {(perm.content_type.app_label, perm.content_type.model, perm.codename): perm for perm in catalog}
    user.user_permissions.remove(*catalog)
    wanted = []
    model_grants = set()
    for key, action in grants:
        screen_perm_obj = by_natural.get(("accounts", "user", screen_codename(key, action)))
        if screen_perm_obj is not None:
            wanted.append(screen_perm_obj)
        for app, model in _BY_KEY[key]["models"]:
            model_grants.add((app, model, f"{action}_{model}"))
    for natural in model_grants:
        perm = by_natural.get(natural)
        if perm is not None:
            wanted.append(perm)
    if wanted:
        user.user_permissions.add(*wanted)
    if not user.access_managed:
        user.access_managed = True
        user.save(update_fields=["access_managed"])


def screens_for_path(path: str) -> list[str]:
    if path.startswith("/tsd/tasks"):
        return ["tsd_receive", "tsd_pick", "tsd_ship"]
    if path.startswith("/print/pallets"):
        return ["web_reports", "tsd_receive"]
    if path.startswith("/shift"):
        return ["web_shift", "tsd_shift"]
    prefixes = (
        ("/print/units", "web_reports"),
        ("/print/printers", "web_masters"),
        ("/orders", "web_orders"),
        ("/tasks", "web_tasks"),
        ("/reports", "web_reports"),
        ("/warehouse", "web_reports"),
        ("/stock", "web_reports"),
        ("/demand", "web_reports"),
        ("/pallets", "web_reports"),
        ("/units", "web_reports"),
        ("/inventory", "web_reports"),
        ("/transform", "web_reports"),
        ("/lots", "web_reports"),
        ("/receipts", "web_shipments"),
        ("/movements", "web_shipments"),
        ("/crossdock", "web_shipments"),
        ("/logistics", "web_logistics"),
        ("/masters", "web_masters"),
        ("/users", "web_masters"),
        ("/staff", "web_masters"),
        ("/positions", "web_masters"),
        ("/office", "web_masters"),
        ("/service", "web_service"),
        ("/tsd/receive", "tsd_receive"),
        ("/tsd/pick", "tsd_pick"),
        ("/tsd/ship", "tsd_ship"),
        ("/tsd/move", "tsd_move"),
        ("/tsd/find", "tsd_find"),
        ("/tsd/inventory", "tsd_inventory"),
    )
    for prefix, screen in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            return [screen]
    return []


def path_action(path: str, method: str) -> str:
    if "/delete" in path:
        return "delete"
    if method == "GET":
        return "view"
    return "change"
