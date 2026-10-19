ROLE_LABELS = {
    "receiver": "Приёмщик",
    "warehouse": "Кладовщик",
    "office": "Офис",
    "admin": "Администратор",
}

UNIT_STATUS = {
    "received": "Принято",
    "on_pallet": "На паллете",
    "in_stock": "На складе",
    "allocated": "Под заказ",
    "shipped": "Отгружено",
    "rejected": "Брак",
}

DOC_STATUS = {
    "open": "Открыт",
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
    "to_pick": "К отбору",
    "to_ship": "К отгрузке",
    "closed": "Закрыт",
}

TASK_WORK_STATUS = {
    "receive": "to_receive",
    "pick": "to_pick",
    "ship": "to_ship",
}


def task_status_choices(kind: str, current: str = "") -> list[tuple[str, str]]:
    work = TASK_WORK_STATUS.get(kind)
    rows = [("open", TASK_STATUS["open"])]
    if work:
        rows.append((work, TASK_STATUS[work]))
    rows.append(("closed", TASK_STATUS["closed"]))
    if current and current not in {key for key, _label in rows}:
        rows.append((current, TASK_STATUS.get(current, current)))
    return rows

PALLET_TARE = {
    "EUR": "15.000",
    "USA": "20.000",
}
