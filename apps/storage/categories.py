def category_ids(pallet) -> set[int] | None:
    found = {item.id for item in pallet.allowed_categories.all()}
    if not found:
        return None
    return found


def allows_consignee(pallet, consignee) -> bool:
    allowed = category_ids(pallet)
    if allowed is None:
        return True
    return bool(consignee and consignee.category_id and consignee.category_id in allowed)
