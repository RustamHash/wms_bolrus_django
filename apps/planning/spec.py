def _grade_fits(lot, line) -> bool:
    if line.grade_id:
        return lot.grade_id == line.grade_id
    order_line = line.order_line
    if order_line is None:
        return True
    from apps.catalog.grade_policy import effective_grade_ids

    consignee = order_line.order.consignee if order_line.order_id else None
    allowed = effective_grade_ids(order_line, consignee)
    if not allowed:
        return True
    return lot.grade_id in allowed


def _caliber_fits(lot, line) -> bool:
    if line.caliber_id:
        return lot.caliber_id == line.caliber_id
    order_line = line.order_line
    if order_line is None or order_line.caliber_mode == "any":
        return True
    if order_line.caliber_mode == "exact":
        return bool(order_line.caliber_id) and lot.caliber_id == order_line.caliber_id
    if order_line.caliber_mode != "range":
        return True
    start = order_line.caliber_from.sort_order if order_line.caliber_from_id else None
    end = order_line.caliber_to.sort_order if order_line.caliber_to_id else None
    caliber = lot.caliber
    if caliber is None or start is None or end is None:
        return False
    return start <= caliber.sort_order <= end


def same_spec(lot, line) -> bool:
    return lot.product_id == line.product_id and _grade_fits(lot, line) and _caliber_fits(lot, line)


def specificity(line) -> tuple[int, int]:
    grade_rank = 0 if line.grade_id else 1
    if line.caliber_id:
        caliber_rank = 0
    else:
        order_line = line.order_line
        caliber_rank = 1 if order_line is not None and order_line.caliber_mode == "range" else 2
    return grade_rank, caliber_rank
