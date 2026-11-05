"""Money helpers: Decimal everywhere, rounded to whole euros for output."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_eur(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def format_number(value: Decimal | int) -> str:
    n = int(round_eur(Decimal(value)))
    sign = "−" if n < 0 else ""
    return f"{sign}{abs(n):,}".replace(",", "\u00a0")


def format_eur_range(lo: Decimal, hi: Decimal) -> str:
    """'1 600–4 000 €' (or a single value when equal after rounding)."""
    if round_eur(lo) == round_eur(hi):
        return format_eur(lo)
    return f"{format_number(lo)}–{format_eur(hi)}"


def format_pct(rate: Decimal, lang: str = "ru", digits: int = 2) -> str:
    """Decimal('0.015') -> '1,5%' (ru) / '1.5%' (en)."""
    value = (Decimal(rate) * 100).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    s = f"{value:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if lang == "ru":
        s = s.replace(".", ",")
    return s + "%"


def format_eur(value: Decimal | int) -> str:
    """82000 -> '82 000 €' (no-break spaces)."""
    n = int(round_eur(Decimal(value)))
    sign = "−" if n < 0 else ""
    return f"{sign}{abs(n):,}".replace(",", "\u00a0") + "\u00a0€"
