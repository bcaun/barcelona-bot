"""Rendering of calculator results (Telegram HTML). No business logic here."""

from __future__ import annotations

from decimal import Decimal

from app.config import Regulation
from app.core.calc import CalcResult, PropertyType, Range, TaxKind, TaxStatus
from app.core.money import format_eur, format_eur_range, format_number, format_pct
from app.i18n import t


def _r(x: Range) -> str:
    return format_eur_range(x.min, x.max)


def block_a(res: CalcResult, lang: str) -> list[str]:
    inp, tax = res.input, res.tax
    pct = lambda r: format_pct(r, lang)  # noqa: E731
    out = [t(lang, "calc.r.block_a")]
    if tax.kind == TaxKind.NEW_BUILD:
        out.append(
            t(
                lang,
                "calc.r.new_build",
                total=format_eur(tax.total),
                price=format_eur(inp.price),
                iva_rate=pct(tax.iva / inp.price),
                iva=format_eur(tax.iva),
                ajd_base=format_eur(tax.ajd_base),
                ajd_rate=pct(tax.ajd / tax.ajd_base),
                ajd=format_eur(tax.ajd),
            )
        )
    elif tax.kind == TaxKind.ITP_GENERAL:
        out.append(
            t(lang, "calc.r.itp_general", base=format_eur(tax.base), total=format_eur(tax.total))
        )
        for line in tax.lines:
            top = line.lower + line.taxable
            if line.upper is None and line.lower > 0:
                out.append(
                    t(
                        lang,
                        "calc.r.itp_line_top",
                        lower=format_eur(line.lower),
                        rate=pct(line.rate),
                        amount=format_eur(line.amount),
                    )
                )
            else:
                out.append(
                    t(
                        lang,
                        "calc.r.itp_line",
                        lower=format_number(line.lower),
                        upper=format_eur(top),
                        rate=pct(line.rate),
                        amount=format_eur(line.amount),
                    )
                )
    else:
        key = "calc.r.itp_reduced" if tax.kind == TaxKind.ITP_REDUCED else "calc.r.itp_large_holder"
        out.append(
            t(lang, key, rate=pct(tax.rate), base=format_eur(tax.base), total=format_eur(tax.total))
        )
    if tax.base_from_reference and inp.reference_value is not None:
        out.append(t(lang, "calc.r.base_from_reference", ref=format_eur(inp.reference_value)))
    if tax.reference_unknown:
        if tax.kind != TaxKind.NEW_BUILD:
            out.append(t(lang, "calc.r.reference_unknown"))
        else:
            out.append(t(lang, "calc.r.reference_unknown_new"))
    for note in tax.notes:
        out.append(t(lang, f"calc.r.note_{note}"))
    out.append("")
    out.append(t(lang, "calc.r.notary", v=_r(res.notary)))
    out.append(t(lang, "calc.r.registry", v=_r(res.registry)))
    out.append(t(lang, "calc.r.gestoria", v=_r(res.gestoria)))
    if res.tasacion:
        out.append(t(lang, "calc.r.tasacion", v=_r(res.tasacion)))
    if res.agency > 0:
        out.append(t(lang, "calc.r.agency", v=format_eur(res.agency)))
    out.append("")
    out.append(t(lang, "calc.r.extra", v=_r(res.extra)))
    out.append(t(lang, "calc.r.total", v=_r(res.total)))
    if res.own_funds is not None and res.loan is not None and inp.mortgage_share:
        out.append(
            t(
                lang,
                "calc.r.own_funds",
                share=format_pct(inp.mortgage_share, lang),
                loan=format_eur(res.loan),
                v=_r(res.own_funds),
            )
        )
    return out


def block_b(res: CalcResult, lang: str) -> list[str]:
    ann = res.annual
    out = [t(lang, "calc.r.block_b")]
    if ann.ibi:
        out.append(t(lang, "calc.r.ibi", v=_r(ann.ibi)))
    else:
        out.append(
            t(
                lang,
                "calc.r.ibi_unknown",
                min=format_pct(ann.ibi_rate.min, lang),
                max=format_pct(ann.ibi_rate.max, lang),
            )
        )
    out.append(t(lang, "calc.r.comunidad"))
    if ann.irnr_applies and ann.irnr_coef is not None and ann.irnr_rate is not None:
        coef, rate = format_pct(ann.irnr_coef, lang), format_pct(ann.irnr_rate, lang)
        if ann.irnr is not None and res.input.cadastral_value:
            out.append(
                t(
                    lang,
                    "calc.r.irnr",
                    vc=format_eur(res.input.cadastral_value),
                    coef=coef,
                    rate=rate,
                    v=format_eur(ann.irnr),
                )
            )
        else:
            out.append(t(lang, "calc.r.irnr_formula", coef=coef, rate=rate))
    return out


def block_c(res: CalcResult, lang: str, reg: Regulation) -> list[str]:
    rent = res.rental
    if rent is None or res.input.monthly_rent is None:
        return []
    y = lambda v: format_pct(v, lang, 1)  # noqa: E731
    out = [
        t(lang, "calc.r.block_c"),
        t(
            lang,
            "calc.r.rent_income",
            month=format_eur(res.input.monthly_rent),
            year=format_eur(rent.annual_rent),
        ),
        t(lang, "calc.r.gross_yield", v=y(rent.gross_yield)),
    ]
    status = res.input.tax_status
    if status == TaxStatus.RESIDENT:
        out.append(t(lang, "calc.r.rent_tax_resident"))
        out.append(t(lang, "calc.r.net_yield_resident", v=y(rent.net_yield)))
    else:
        key = "calc.r.rent_tax_eu" if status == TaxStatus.EU else "calc.r.rent_tax_non_eu"
        out.append(
            t(
                lang,
                key,
                rate=format_pct(rent.tax_rate or Decimal(0), lang),
                v=format_eur(rent.tax or 0),
            )
        )
        if not res.annual.ibi:
            out.append(t(lang, "calc.r.rent_tax_no_ibi"))
        out.append(t(lang, "calc.r.net_yield", v=y(rent.net_yield)))
    municipality = res.input.municipality
    warnings = []
    if reg.is_stressed(municipality):
        warnings.append("⚠️ " + reg.text("stressed_market", lang))
        if reg.links.get("rent_index"):
            warnings.append(t(lang, "calc.r.rent_index_link", url=reg.links["rent_index"]))
    if municipality.lower() == "barcelona" and reg.text("tourist_licence_barcelona", lang):
        warnings.append("⚠️ " + reg.text("tourist_licence_barcelona", lang))
    if warnings:
        out.append("")
        out.extend(warnings)
    return out


def render_result(
    res: CalcResult, lang: str, reg: Regulation, verified_at: str, locked: bool = False
) -> str:
    type_label = t(lang, f"calc.a.{res.input.property_type.value}")
    parts = [t(lang, "calc.r.title", type=type_label, price=format_eur(res.input.price)), ""]
    parts += block_a(res, lang)
    if locked:
        parts += ["", t(lang, "calc.r.locked")]
    else:
        parts += [""] + block_b(res, lang)
        c = block_c(res, lang, reg)
        if c:
            parts += [""] + c
    parts += ["", t(lang, "calc.r.disclaimer"), t(lang, "calc.r.verified", date=verified_at)]
    return "\n".join(parts)


def lead_summary(res: CalcResult, lang: str) -> dict[str, str]:
    """Flat human-readable dict for the admin card and CSV."""
    inp = res.input
    lab = lambda k: t(lang, f"calc.lead.{k}")  # noqa: E731
    d: dict[str, str] = {
        lab("type"): t(lang, f"calc.a.{inp.property_type.value}"),
        lab("price"): format_eur(inp.price),
    }
    if inp.reference_value is not None:
        d[lab("reference_value")] = format_eur(inp.reference_value)
    d[lab("buyer")] = t(lang, f"calc.a.{inp.buyer.value}")
    d[lab("tax_kind")] = t(lang, f"calc.lead.kinds.{res.tax.kind.value}")
    d[lab("tax")] = format_eur(res.tax.total)
    d[lab("extra")] = _r(res.extra)
    if inp.mortgage_share and res.own_funds is not None:
        d[lab("mortgage")] = format_pct(inp.mortgage_share, lang)
        d[lab("own_funds")] = _r(res.own_funds)
    d[lab("tax_status")] = t(lang, f"calc.a.{inp.tax_status.value}")
    if inp.cadastral_value:
        d[lab("cadastral_value")] = format_eur(inp.cadastral_value)
    if inp.monthly_rent and res.rental:
        d[lab("monthly_rent")] = format_eur(inp.monthly_rent)
        d[lab("gross_yield")] = format_pct(res.rental.gross_yield, lang, 1)
    return d


__all__ = ["render_result", "lead_summary", "PropertyType"]
