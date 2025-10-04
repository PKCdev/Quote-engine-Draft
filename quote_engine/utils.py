from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def to_decimal(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if x is None:
        return Decimal(0)
    return Decimal(str(x))


def money(amount: Decimal, symbol: str = "$", places: int = 2) -> str:
    q = Decimal(10) ** -places
    val = amount.quantize(q, rounding=ROUND_HALF_UP)
    parts = f"{val:.{places}f}".split(".")
    whole = parts[0]
    frac = parts[1] if len(parts) > 1 else "00"
    sign = ""
    if whole.startswith("-"):
        sign = "-"
        whole = whole[1:]
    whole_with_commas = "{:,}".format(int(whole))
    return f"{sign}{symbol}{whole_with_commas}.{frac}"

