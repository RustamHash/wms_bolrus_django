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


def grade_for_consignee(consignee):
    """Сортность по категории грузополучателя. Пусто, если категория не задана или сорт ей запрещён."""
    if consignee is None or not consignee.category_id:
        return None
    grade = consignee.category.grade
    party = allowed_grade_ids(consignee)
    if party is not None and grade.id not in party:
        return None
    return grade


def _write_category_grade(line, grade) -> None:
    from apps.inventory.models import Movement

    if Movement.objects.filter(task_line__order_line_id=line.id).exists():
        return
    current = {item.id for item in line.grades.all()}
    if current != {grade.id}:
        line.grades.set([grade])
        line._effective_grades = ({grade.id},)
    for task_line in line.task_lines.all():
        if task_line.grade_id != grade.id:
            task_line.grade_id = grade.id
            task_line.save(update_fields=["grade"])


def stamp_order_grades(order) -> None:
    grade = grade_for_consignee(order.consignee)
    if grade is None:
        return
    lines = order.lines.filter(for_task=True, grade_manual=False).prefetch_related("grades", "task_lines")
    for line in lines:
        _write_category_grade(line, grade)


def fill_consignee_orders(consignee) -> None:
    from apps.planning.models import OrderLine

    grade = grade_for_consignee(consignee)
    if grade is None:
        return
    lines = OrderLine.objects.filter(
        order__consignee=consignee,
        order__status="open",
        for_task=True,
        grade_manual=False,
    ).prefetch_related("grades", "task_lines")
    for line in lines:
        _write_category_grade(line, grade)
