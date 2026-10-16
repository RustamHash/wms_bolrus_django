from decimal import Decimal


def as_kg(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.001"))


def format_kg(value) -> str:
    return f"{as_kg(value):.3f}".replace(".", ",")
