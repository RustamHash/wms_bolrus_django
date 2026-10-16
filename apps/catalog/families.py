import re

CRITICAL_PRODUCTS = [
    ("Т-001", "Авокадо"),
    ("Т-002", "Апельсины"),
    ("Т-003", "Бананы"),
    ("Т-004", "Грибы шампиньоны"),
    ("Т-005", "Груши"),
    ("Т-006", "Инжир"),
    ("Т-007", "Кабачки"),
    ("Т-008", "Капуста белокочанная"),
    ("Т-009", "Капуста краснокочанная"),
    ("Т-010", "Капуста пекинская"),
    ("Т-011", "Киви"),
    ("Т-012", "Лимон"),
    ("Т-013", "Лук зеленый"),
    ("Т-014", "Мандарины"),
    ("Т-015", "Огурцы"),
    ("Т-016", "Огурцы соленные"),
    ("Т-017", "Перец болгарский"),
    ("Т-018", "Перец сладкий"),
    ("Т-019", "Петрушка зелень"),
    ("Т-020", "Редис"),
    ("Т-021", "Салат листовой"),
    ("Т-022", "Томаты"),
    ("Т-023", "Укроп зелень"),
    ("Т-024", "Филе сельди"),
    ("Т-025", "Щавель"),
]
FAMILY_CODE = {name: code for code, name in CRITICAL_PRODUCTS}


def _base_name(name: str) -> str:
    cut = name
    for marker in ("ГОСТ", "ТУ", "СТО", "ТН ВЭД"):
        index = cut.find(marker)
        if index != -1:
            cut = cut[:index]
    cut = re.sub(r"\([^)]*\)", " ", cut)
    cut = re.sub(r"\bсвеж\w*\b", " ", cut, flags=re.IGNORECASE)
    return " ".join(cut.split())


def plain_title(name: str) -> str:
    text = " ".join((name or "").split())
    text = re.sub(r"\s+,", ",", text)
    chars = []
    started = False
    for char in text:
        if char.isalpha():
            chars.append(char.upper() if not started else char.lower())
            started = True
        else:
            chars.append(char)
    text = "".join(chars)
    text = re.sub(r"\bрепч\b", "репчатый", text, flags=re.IGNORECASE)
    if re.search(r"капуст", text, flags=re.IGNORECASE):
        text = re.sub(r"\bб/к\b", "белокочанная", text, flags=re.IGNORECASE)
    return " ".join(text.split())[:120]


def pack_note(name: str) -> str:
    match = re.search(r"\(([^)]*сетк[^)]*)\)", name or "", flags=re.IGNORECASE)
    if not match:
        return ""
    note = re.sub(r"(\d)\s*кг", r"\1 кг", match.group(1).lower())
    return " ".join(note.split())


def with_pack(name: str, title: str) -> str:
    note = pack_note(name)
    if note and note not in title.lower():
        title = f"{title}, {note}"
    return title[:120]


def critical_family(name: str) -> str:
    if name.lower().startswith("филе сельди"):
        return "Филе сельди"
    lowered = name.lower()
    if "заморож" in lowered:
        return "Замороженная продукция"
    family = _base_name(name)
    if "очищен" in lowered:
        family = re.sub(r"\b(?:сыр\w*|поздн\w*)\b", " ", family, flags=re.IGNORECASE)
        if re.search(r"\bкартофел", family, flags=re.IGNORECASE):
            family = re.sub(r"\bстолов\w*\b", " ", family, flags=re.IGNORECASE)
        family = " ".join(family.split())
    return with_pack(name, plain_title(family))
