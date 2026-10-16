def allowed_grade_ids(consignee) -> set[int] | None:
    if consignee is None:
        return None
    ids = {item.id for item in consignee.allowed_grades.all()}
    return ids or None


def effective_grade_ids(line, consignee) -> set[int] | None:
    cached = getattr(line, "_effective_grades", None)
    if cached is not None:
        return cached[0]
    line_ids = {item.id for item in line.grades.all()} or None
    party_ids = allowed_grade_ids(consignee)
    if line_ids is None:
        result = party_ids
    elif party_ids is None:
        result = line_ids
    else:
        result = line_ids & party_ids
    line._effective_grades = (result,)
    return result


def effective_grade_names(line, consignee) -> str:
    ids = effective_grade_ids(line, consignee)
    if not ids:
        return ""
    found = {}
    for item in line.grades.all():
        found[item.id] = item
    if consignee is not None:
        for item in consignee.allowed_grades.all():
            found.setdefault(item.id, item)
    chosen = sorted((found[item_id] for item_id in ids if item_id in found), key=lambda item: (item.sort_order, item.name))
    return ", ".join(item.name for item in chosen)


def grade_names(consignee) -> str:
    if consignee is None:
        return ""
    grades = sorted(consignee.allowed_grades.all(), key=lambda item: (item.sort_order, item.name))
    return ", ".join(item.name for item in grades)
