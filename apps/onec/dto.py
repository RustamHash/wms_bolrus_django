from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class PartyRef:
    ref_key: str = ""
    code: str = ""
    name: str = ""


@dataclass(slots=True)
class ProductRef:
    ref_key: str = ""
    code: str = ""
    name: str = ""
    article: str = ""


@dataclass(slots=True)
class OrderLineDTO:
    line_number: int
    product: ProductRef
    qty: Decimal
    unit: str = ""
    content: str = ""
    price: Decimal | None = None
    amount: Decimal | None = None
    weight: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class OrderDTO:
    kind: str  # purchase | customer
    ref_key: str
    number: str
    date: datetime | None
    posted: bool
    party: PartyRef
    organization: PartyRef = field(default_factory=PartyRef)
    consignee: PartyRef = field(default_factory=PartyRef)
    delivery_address: str = ""
    state: str = ""
    comment: str = ""
    ship_date: date | None = None
    receive_date: date | None = None
    amount: Decimal | None = None
    lines: list[OrderLineDTO] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
