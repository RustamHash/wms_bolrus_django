def document_number(prefix: str, ident: int) -> str:
    return f"{prefix}-{ident:06d}"


def barcode(prefix: str, ident: int) -> str:
    return f"{prefix}{ident:06d}"
