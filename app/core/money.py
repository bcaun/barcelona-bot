"""Money helpers: Decimal everywhere, rounded to whole euros for output."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_eur(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def format_eur(value: Decimal | int) -> str:
    """82000 -> '82 000 €' (no-break spaces)."""
    n = int(round_eur(Decimal(value)))
    sign = "−" if n < 0 else ""
    return f"{sign}{abs(n):,}".replace(",", "\u00a0") + "\u00a0€"
