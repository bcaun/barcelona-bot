"""Purchase & ownership cost calculator (Catalonia). Pure functions, no Telegram.

All rates and thresholds come from config/taxes.yaml via TaxConfig.from_dict().
Amounts are Decimal; rounding happens only on output (see app.core.money).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------- enums & helpers


class PropertyType(StrEnum):
    RESALE = "resale"
    NEW = "new"


class Buyer(StrEnum):
    PRIVATE = "private"
    COMPANY = "company"


class TaxStatus(StrEnum):
    RESIDENT = "resident"
    EU = "eu_eea"
    NON_EU = "non_eu"


class Family(StrEnum):
    NONE = "none"
    LARGE = "large_family"
    SINGLE = "single_parent"


class TaxKind(StrEnum):
    ITP_GENERAL = "itp_general"
    ITP_REDUCED = "itp_reduced"
    ITP_LARGE_HOLDER = "itp_large_holder"
    NEW_BUILD = "new_build"


def D(value: Any) -> Decimal:
    """YAML floats -> exact Decimal (0.1 -> Decimal('0.1'))."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


ZERO = Decimal(0)


@dataclass(frozen=True)
class Range:
    min: Decimal
    max: Decimal

    def __add__(self, other: Range | Decimal) -> Range:
        if isinstance(other, Range):
            return Range(self.min + other.min, self.max + other.max)
        return Range(self.min + other, self.max + other)

    @property
    def mid(self) -> Decimal:
        return (self.min + self.max) / 2


# ---------------------------------------------------------------- config


@dataclass(frozen=True)
class Bracket:
    up_to: Decimal | None
    rate: Decimal


@dataclass(frozen=True)
class PctFee:
    min_pct: Decimal
    max_pct: Decimal
    min_eur: Decimal

    def range_for(self, base: Decimal) -> Range:
        return Range(max(self.min_eur, base * self.min_pct), max(self.min_eur, base * self.max_pct))


@dataclass(frozen=True)
class TaxConfig:
    verified_at: str
    brackets: tuple[Bracket, ...]
    reduced_rate: Decimal
    reduced_conditions: frozenset[str]
    age_max: int
    disability_min_pct: int
    income_limit: Decimal
    income_extra_per_child: Decimal
    children_minimum: dict[Family, int]
    large_holder_rate: Decimal
    large_holder_dwellings_gt: int
    large_holder_m2_gt: int
    large_holder_note_key: str
    iva: Decimal
    ajd: Decimal
    ajd_uses_reference_value: bool
    notary: PctFee
    registry: PctFee
    gestoria: Range
    tasacion: Range
    agency_pct: Decimal
    ibi_rate: Range
    irnr_coef_default: Decimal
    irnr_coef_by_municipality: dict[str, Decimal]
    irnr_rates: dict[TaxStatus, Decimal]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaxConfig:
        itp = d["itp_resale"]
        red = itp["reduced_habitual_residence"]
        lh = itp["large_holder"]
        nb = d["new_build"]
        fees = d["purchase_fees"]
        ann = d["annual"]
        brackets = tuple(
            Bracket(None if b["up_to"] is None else D(b["up_to"]), D(b["rate"]))
            for b in itp["brackets"]
        )
        if not brackets or brackets[-1].up_to is not None:
            raise ValueError("taxes.yaml: the last ITP bracket must have up_to: null")
        cmin = red.get("children_minimum", {})

        def pct_fee(x: dict[str, Any]) -> PctFee:
            return PctFee(D(x["min_pct"]), D(x["max_pct"]), D(x.get("min_eur", 0)))

        return cls(
            verified_at=str(d.get("verified_at", "")),
            brackets=brackets,
            reduced_rate=D(red["rate"]),
            reduced_conditions=frozenset(red.get("conditions", [])),
            age_max=int(red.get("age_max", 35)),
            disability_min_pct=int(red.get("disability_min_pct", 65)),
            income_limit=D(red["income_limit_irpf_base"]),
            income_extra_per_child=D(red.get("income_limit_extra_per_child", 0)),
            children_minimum={
                Family.LARGE: int(cmin.get("large_family", 3)),
                Family.SINGLE: int(cmin.get("single_parent", 1)),
            },
            large_holder_rate=D(lh["rate"]),
            large_holder_dwellings_gt=int(lh["thresholds"]["dwellings_gt"]),
            large_holder_m2_gt=int(lh["thresholds"]["built_m2_gt"]),
            large_holder_note_key=str(lh.get("note_key", "large_holder_exclusions_not_modelled")),
            iva=D(nb["iva_dwelling"]),
            ajd=D(nb["ajd"]),
            ajd_uses_reference_value=bool(nb.get("ajd_base_uses_reference_value", True)),
            notary=pct_fee(fees["notary"]),
            registry=pct_fee(fees["registry"]),
            gestoria=Range(D(fees["gestoria"]["min_eur"]), D(fees["gestoria"]["max_eur"])),
            tasacion=Range(D(fees["tasacion"]["min_eur"]), D(fees["tasacion"]["max_eur"])),
            agency_pct=D(fees.get("agency_buyer_pct", 0)),
            ibi_rate=Range(D(ann["ibi_estimate_rate"]["min"]), D(ann["ibi_estimate_rate"]["max"])),
            irnr_coef_default=D(ann["irnr_imputed"]["coefficient_default"]),
            irnr_coef_by_municipality={
                k.lower(): D(v)
                for k, v in (ann["irnr_imputed"].get("coefficient_by_municipality") or {}).items()
            },
            irnr_rates={
                TaxStatus.EU: D(ann["irnr_rates"]["eu_eea"]),
                TaxStatus.NON_EU: D(ann["irnr_rates"]["non_eu"]),
            },
        )


# ---------------------------------------------------------------- input


@dataclass
class CalcInput:
    property_type: PropertyType
    price: Decimal
    buyer: Buyer = Buyer.PRIVATE
    reference_value: Decimal | None = None
    habitual: bool = False
    family: Family = Family.NONE
    children: int = 0
    age_le_limit: bool = False
    disability: bool = False
    income_within_limit: bool = False
    large_holder: bool = False
    mortgage_share: Decimal | None = None  # 0..1, None = no mortgage
    tax_status: TaxStatus = TaxStatus.RESIDENT
    cadastral_value: Decimal | None = None
    monthly_rent: Decimal | None = None
    municipality: str = "barcelona"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in asdict(self).items():
            out[k] = (
                str(v) if isinstance(v, Decimal) else (v.value if isinstance(v, StrEnum) else v)
            )
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CalcInput:
        def dec(k: str) -> Decimal | None:
            return None if d.get(k) in (None, "") else Decimal(str(d[k]))

        return cls(
            property_type=PropertyType(d["property_type"]),
            price=Decimal(str(d["price"])),
            buyer=Buyer(d.get("buyer", "private")),
            reference_value=dec("reference_value"),
            habitual=bool(d.get("habitual", False)),
            family=Family(d.get("family", "none")),
            children=int(d.get("children", 0)),
            age_le_limit=bool(d.get("age_le_limit", False)),
            disability=bool(d.get("disability", False)),
            income_within_limit=bool(d.get("income_within_limit", False)),
            large_holder=bool(d.get("large_holder", False)),
            mortgage_share=dec("mortgage_share"),
            tax_status=TaxStatus(d.get("tax_status", "resident")),
            cadastral_value=dec("cadastral_value"),
            monthly_rent=dec("monthly_rent"),
            municipality=str(d.get("municipality", "barcelona")),
        )


# ---------------------------------------------------------------- input parsing


class AmountParseError(ValueError):
    pass


_SUFFIXES = {
    "k": 1_000, "к": 1_000, "тыс": 1_000, "т": 1_000,
    "m": 1_000_000, "м": 1_000_000, "млн": 1_000_000, "mln": 1_000_000,
}  # fmt: skip
_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)*)(k|к|тыс|т|mln|млн|m|м)?")


def parse_amount(text: str) -> Decimal:
    """'450000', '450 000', '450 000 €', '450.000', '450k', '1,2 млн' -> Decimal."""
    s = (text or "").strip().lower()
    for token in ("€", "eur", "euro", "евро", "евр"):
        s = s.replace(token, "")
    s = re.sub(r"[\s  '’_]", "", s).rstrip(".")
    m = _AMOUNT_RE.fullmatch(s)
    if not m:
        raise AmountParseError(text)
    num, suffix = m.groups()
    if suffix:
        if num.count(".") + num.count(",") > 1:
            raise AmountParseError(text)
        value = Decimal(num.replace(",", ".")) * _SUFFIXES[suffix]
    elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", num):
        value = Decimal(re.sub(r"[.,]", "", num))  # thousands separators
    elif re.fullmatch(r"\d+(?:[.,]\d{1,2})?", num):
        value = Decimal(num.replace(",", "."))
    else:
        raise AmountParseError(text)
    if value <= 0:
        raise AmountParseError(text)
    return value


def parse_percent(text: str) -> Decimal:
    """'70', '70%', '70 %' -> Decimal('0.7'); valid 1..100."""
    s = re.sub(r"[\s%]", "", (text or "")).replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        raise AmountParseError(text)
    value = Decimal(s)
    if not (0 < value <= 100):
        raise AmountParseError(text)
    return value / 100


# ---------------------------------------------------------------- purchase tax


@dataclass
class BracketLine:
    lower: Decimal
    upper: Decimal | None
    rate: Decimal
    taxable: Decimal
    amount: Decimal


@dataclass
class PurchaseTax:
    kind: TaxKind
    base: Decimal
    total: Decimal
    base_from_reference: bool = False  # base = valor de referencia (> price)
    reference_unknown: bool = False
    lines: list[BracketLine] = field(default_factory=list)  # ITP progressive breakdown
    rate: Decimal | None = None  # flat rate (reduced / large holder)
    iva: Decimal | None = None
    ajd: Decimal | None = None
    ajd_base: Decimal | None = None
    notes: list[str] = field(default_factory=list)  # i18n note keys


def progressive_tax(base: Decimal, brackets: tuple[Bracket, ...]) -> list[BracketLine]:
    lines: list[BracketLine] = []
    lower = ZERO
    for b in brackets:
        if base <= lower:
            break
        upper = b.up_to
        top = base if upper is None else min(base, upper)
        taxable = top - lower
        lines.append(BracketLine(lower, upper, b.rate, taxable, taxable * b.rate))
        if upper is None:
            break
        lower = upper
    return lines


def income_limit(cfg: TaxConfig, family: Family, children: int) -> Decimal:
    limit = cfg.income_limit
    if family != Family.NONE:
        extra_children = max(0, children - cfg.children_minimum[family])
        limit += cfg.income_extra_per_child * extra_children
    return limit


def reduced_rate_applies(inp: CalcInput, cfg: TaxConfig) -> bool:
    if inp.property_type != PropertyType.RESALE or inp.buyer != Buyer.PRIVATE:
        return False
    if not inp.habitual or inp.large_holder:
        return False
    conds = cfg.reduced_conditions
    personal = (
        ("age_le_35" in conds and inp.age_le_limit)
        or ("large_or_single_parent_family" in conds and inp.family != Family.NONE)
        or ("disability_ge_65" in conds and inp.disability)
    )
    return personal and inp.income_within_limit


def purchase_tax(inp: CalcInput, cfg: TaxConfig) -> PurchaseTax:
    ref = inp.reference_value
    base = max(inp.price, ref) if ref is not None else inp.price
    from_ref = ref is not None and ref > inp.price

    if inp.property_type == PropertyType.NEW:
        iva = inp.price * cfg.iva
        ajd_base = base if cfg.ajd_uses_reference_value else inp.price
        ajd = ajd_base * cfg.ajd
        return PurchaseTax(
            kind=TaxKind.NEW_BUILD,
            base=inp.price,
            total=iva + ajd,
            iva=iva,
            ajd=ajd,
            ajd_base=ajd_base,
            base_from_reference=from_ref and cfg.ajd_uses_reference_value,
            reference_unknown=ref is None and cfg.ajd_uses_reference_value,
        )

    common = {"base": base, "base_from_reference": from_ref, "reference_unknown": ref is None}
    if inp.large_holder:
        return PurchaseTax(
            kind=TaxKind.ITP_LARGE_HOLDER,
            total=base * cfg.large_holder_rate,
            rate=cfg.large_holder_rate,
            notes=[cfg.large_holder_note_key],
            **common,
        )
    if reduced_rate_applies(inp, cfg):
        return PurchaseTax(
            kind=TaxKind.ITP_REDUCED,
            total=base * cfg.reduced_rate,
            rate=cfg.reduced_rate,
            **common,
        )
    lines = progressive_tax(base, cfg.brackets)
    return PurchaseTax(
        kind=TaxKind.ITP_GENERAL,
        total=sum((x.amount for x in lines), ZERO),
        lines=lines,
        **common,
    )


# ---------------------------------------------------------------- annual & rental


def irnr_coefficient(cfg: TaxConfig, municipality: str) -> Decimal:
    return cfg.irnr_coef_by_municipality.get(municipality.lower(), cfg.irnr_coef_default)


def irnr_imputed(cadastral_value: Decimal, coef: Decimal, rate: Decimal) -> Decimal:
    return cadastral_value * coef * rate


@dataclass
class Annual:
    ibi: Range | None  # None: no cadastral value given
    ibi_rate: Range
    irnr_applies: bool  # non-resident without long-term rental
    irnr_coef: Decimal | None = None
    irnr_rate: Decimal | None = None
    irnr: Decimal | None = None  # None: no cadastral value given


@dataclass
class Rental:
    annual_rent: Decimal
    investment: Decimal  # price + mid purchase costs
    gross_yield: Decimal
    tax: Decimal | None  # None for residents (IRPF — on consultation)
    tax_rate: Decimal | None
    tax_on_net: bool  # EU/EEA: on rent minus IBI; non-EU: on gross
    ibi_deducted: Decimal
    net_income: Decimal
    net_yield: Decimal


# ---------------------------------------------------------------- full result


@dataclass
class CalcResult:
    input: CalcInput
    tax: PurchaseTax
    notary: Range
    registry: Range
    gestoria: Range
    tasacion: Range | None
    agency: Decimal
    extra: Range
    total: Range
    loan: Decimal | None
    own_funds: Range | None
    annual: Annual
    rental: Rental | None


def calculate(inp: CalcInput, cfg: TaxConfig) -> CalcResult:
    tax = purchase_tax(inp, cfg)
    notary = cfg.notary.range_for(inp.price)
    registry = cfg.registry.range_for(inp.price)
    tasacion = cfg.tasacion if inp.mortgage_share else None
    agency = inp.price * cfg.agency_pct
    extra = (
        notary + registry + cfg.gestoria + (tasacion or Range(ZERO, ZERO)) + (tax.total + agency)
    )
    total = extra + inp.price
    loan = inp.price * inp.mortgage_share if inp.mortgage_share else None
    own_funds = extra + (inp.price - loan) if loan is not None else None

    non_resident = inp.tax_status != TaxStatus.RESIDENT
    renting = bool(inp.monthly_rent)
    vc = inp.cadastral_value
    ibi = Range(vc * cfg.ibi_rate.min, vc * cfg.ibi_rate.max) if vc else None
    annual = Annual(ibi=ibi, ibi_rate=cfg.ibi_rate, irnr_applies=non_resident and not renting)
    if annual.irnr_applies:
        annual.irnr_coef = irnr_coefficient(cfg, inp.municipality)
        annual.irnr_rate = cfg.irnr_rates[inp.tax_status]
        if vc:
            annual.irnr = irnr_imputed(vc, annual.irnr_coef, annual.irnr_rate)

    rental = None
    if renting and inp.monthly_rent:
        annual_rent = inp.monthly_rent * 12
        investment = inp.price + extra.mid
        ibi_mid = ibi.mid if ibi else ZERO
        tax_rate = cfg.irnr_rates.get(inp.tax_status) if non_resident else None
        tax_on_net = inp.tax_status == TaxStatus.EU
        rent_tax: Decimal | None = None
        if tax_rate is not None:
            taxable = max(ZERO, annual_rent - ibi_mid) if tax_on_net else annual_rent
            rent_tax = taxable * tax_rate
        net_income = annual_rent - ibi_mid - (rent_tax or ZERO)
        rental = Rental(
            annual_rent=annual_rent,
            investment=investment,
            gross_yield=annual_rent / investment,
            tax=rent_tax,
            tax_rate=tax_rate,
            tax_on_net=tax_on_net,
            ibi_deducted=ibi_mid,
            net_income=net_income,
            net_yield=net_income / investment,
        )

    return CalcResult(
        input=inp,
        tax=tax,
        notary=notary,
        registry=registry,
        gestoria=cfg.gestoria,
        tasacion=tasacion,
        agency=agency,
        extra=extra,
        total=total,
        loan=loan,
        own_funds=own_funds,
        annual=annual,
        rental=rental,
    )
