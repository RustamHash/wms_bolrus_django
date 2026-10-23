ROLE_LABELS = {
    "none": "Без доступа",
    "receiver": "Приёмщик",
    "warehouse": "Кладовщик",
    "office": "Офис",
    "admin": "Администратор",
    "employee": "Сотрудник",
}

TSD_ACCESS = {
    "receive": "Приёмка",
    "pick": "Отбор",
    "ship": "Отгрузка",
    "move": "Перемещение",
}

UNIT_STATUS = {
    "received": "Принято",
    "on_pallet": "На паллете",
    "in_stock": "На складе",
    "allocated": "Под заказ",
    "shipped": "Отгружено",
    "rejected": "Брак",
    "transformed": "Переработано",
}

DOC_STATUS = {
    "open": "Открыт",
    "partial": "Отгружен частично",
    "closed": "Закрыт",
}

PURCHASE_STATUS = {
    "draft": "Черновик",
    "incoming": "К поступлению",
    "closed": "Закрыт",
}

PALLET_STATUS = {
    "open": "Открыта",
    "closed": "Закрыта",
}

PALLET_KIND = {
    "inbound": "Вход",
    "outbound": "Выход",
    "loose": "Без паллета",
}

PALLET_TYPE = {
    "EUR": "Евро",
    "USA": "Американец",
}

TASK_KIND = {
    "receive": "Приёмка",
    "pick": "Отбор",
    "ship": "На отгрузку",
}

TASK_STATUS = {
    "open": "Открыт",
    "to_receive": "К приемке",
    "assigned": "Назначено",
    "in_work": "В работе",
    "accepted": "Принят",
    "to_pick": "К отбору",
    "created": "Создана",
    "partial": "Частично спланирована",
    "planned": "Спланирована",
    "picked": "Отобрано",
    "short": "Не хватает",
    "to_ship": "К отгрузке",
    "closed": "Закрыт",
}

TASK_WORK_STATUS = {
    "receive": "to_receive",
    "pick": "created",
    "ship": "to_ship",
}


def task_status_choices(kind: str, current: str = "") -> list[tuple[str, str]]:
    if kind == "pick":
        rows = [
            ("created", TASK_STATUS["created"]),
            ("partial", TASK_STATUS["partial"]),
            ("planned", TASK_STATUS["planned"]),
            ("in_work", TASK_STATUS["in_work"]),
            ("picked", TASK_STATUS["picked"]),
            ("closed", TASK_STATUS["closed"]),
        ]
        if current and current not in {key for key, _label in rows}:
            rows.append((current, TASK_STATUS.get(current, current)))
        return rows
    work = TASK_WORK_STATUS.get(kind)
    rows = [("open", TASK_STATUS["open"])]
    if work:
        rows.append((work, TASK_STATUS[work]))
    if kind == "receive":
        rows.append(("assigned", TASK_STATUS["assigned"]))
        rows.append(("in_work", TASK_STATUS["in_work"]))
        rows.append(("accepted", TASK_STATUS["accepted"]))
    rows.append(("closed", TASK_STATUS["closed"]))
    if current and current not in {key for key, _label in rows}:
        rows.append((current, TASK_STATUS.get(current, current)))
    return rows

PALLET_TARE = {
    "EUR": "15.000",
    "USA": "20.000",
}
