"""Mandatory test cases from SPEC.md 5.4 + parsing + config-driven behaviour."""

import copy
from decimal import Decimal

import pytest

from app.config import load_yaml
from app.core.calc import (
    AmountParseError,
    Buyer,
    CalcInput,
    Family,
    PropertyType,
    TaxConfig,
    TaxKind,
    TaxStatus,
    calculate,
    income_limit,
    irnr_coefficient,
    irnr_imputed,
    parse_amount,
    parse_percent,
    purchase_tax,
)

RAW = load_yaml("taxes.yaml")
CFG = TaxConfig.from_dict(RAW)


def resale(price, **kw):
    return CalcInput(property_type=PropertyType.RESALE, price=Decimal(price), **kw)


@pytest.mark.parametrize(
    ("price", "expected", "parts"),
    [
        (800_000, 82_000, [60_000, 22_000]),
        (1_000_000, 105_000, [60_000, 33_000, 12_000]),
        (2_000_000, 230_000, [60_000, 33_000, 72_000, 65_000]),
    ],
)
def test_itp_general_scale(price, expected, parts):
    t = purchase_tax(resale(price), CFG)
    assert t.kind == TaxKind.ITP_GENERAL
    assert t.total == expected
    assert [x.amount for x in t.lines] == parts


def _young(income):
    lim = income_limit(CFG, Family.NONE, 0)
    return resale(
        300_000,
        habitual=True,
        age_le_limit=CFG.age_max >= 30,
        income_within_limit=Decimal(income) <= lim,
    )


def test_itp_reduced_ok():
    t = purchase_tax(_young(30_000), CFG)
    assert (t.kind, t.total) == (TaxKind.ITP_REDUCED, 15_000)


def test_itp_reduced_fails_on_income():
    t = purchase_tax(_young(40_000), CFG)
    assert (t.kind, t.total) == (TaxKind.ITP_GENERAL, 30_000)


def test_itp_reduced_not_for_company_or_non_habitual():
    assert (
        purchase_tax(
            resale(
                300_000,
                buyer=Buyer.COMPANY,
                habitual=True,
                age_le_limit=True,
                income_within_limit=True,
            ),
            CFG,
        ).total
        == 30_000
    )
    assert (
        purchase_tax(
            resale(300_000, habitual=False, age_le_limit=True, income_within_limit=True), CFG
        ).total
        == 30_000
    )


def test_reference_value_higher_than_price():
    t = purchase_tax(resale(500_000, reference_value=Decimal(550_000)), CFG)
    assert (t.base, t.total, t.base_from_reference) == (550_000, 55_000, True)


def test_reference_value_lower_than_price_ignored():
    t = purchase_tax(resale(500_000, reference_value=Decimal(450_000)), CFG)
    assert (t.base, t.total, t.base_from_reference) == (500_000, 50_000, False)


def test_large_holder():
    t = purchase_tax(resale(400_000, large_holder=True), CFG)
    assert (t.kind, t.total) == (TaxKind.ITP_LARGE_HOLDER, 80_000)
    assert t.notes == ["large_holder_exclusions_not_modelled"]


def test_new_build():
    t = purchase_tax(CalcInput(property_type=PropertyType.NEW, price=Decimal(400_000)), CFG)
    assert (t.iva, t.ajd, t.total) == (40_000, 6_000, 46_000)


@pytest.mark.parametrize(("status", "expected"), [(TaxStatus.NON_EU, 720), (TaxStatus.EU, 570)])
def test_irnr_imputed(status, expected):
    coef = irnr_coefficient(CFG, "barcelona")
    assert irnr_imputed(Decimal(150_000), coef, CFG.irnr_rates[status]) == expected
    r = calculate(resale(400_000, tax_status=status, cadastral_value=Decimal(150_000)), CFG)
    assert r.annual.irnr == expected


def test_income_limit_children():
    assert income_limit(CFG, Family.NONE, 5) == 36_000
    m = CFG.children_minimum[Family.LARGE]
    assert income_limit(CFG, Family.LARGE, m) == 36_000
    assert income_limit(CFG, Family.LARGE, m + 2) == 36_000 + 2 * 14_000


def test_full_result_with_mortgage_and_rent():
    inp = resale(
        400_000,
        mortgage_share=Decimal("0.6"),
        tax_status=TaxStatus.NON_EU,
        cadastral_value=Decimal(100_000),
        monthly_rent=Decimal(1_500),
    )
    r = calculate(inp, CFG)
    assert r.tax.total == 40_000
    assert r.notary.min == 800 and r.notary.max == 2_000  # 0.2%..0.5% of 400k
    assert r.registry.min == 400 and r.registry.max == 1_000
    assert r.tasacion is not None and r.loan == 240_000
    assert r.extra.min == 40_000 + 800 + 400 + 300 + 300
    assert r.own_funds.min == r.extra.min + 160_000
    assert r.total.max == r.extra.max + 400_000
    assert r.annual.irnr_applies is False  # renting -> no imputed income
    assert r.rental.tax == 18_000 * Decimal("0.24")  # non-EU: on gross
    ibi_mid = (500 + 800) / Decimal(2)
    assert r.rental.net_income == 18_000 - ibi_mid - r.rental.tax


def test_rental_eu_on_net_and_resident_no_tax():
    eu = calculate(
        resale(
            400_000,
            tax_status=TaxStatus.EU,
            cadastral_value=Decimal(100_000),
            monthly_rent=Decimal(1_000),
        ),
        CFG,
    )
    assert eu.rental.tax == (12_000 - 650) * Decimal("0.19")
    res = calculate(resale(400_000, monthly_rent=Decimal(1_000)), CFG)
    assert res.rental.tax is None and res.annual.irnr_applies is False


def test_min_fees_apply_for_cheap_property():
    r = calculate(resale(100_000), CFG)
    assert r.notary.min == 600 and r.registry.min == 300


def test_rate_change_in_config_changes_result():
    raw = copy.deepcopy(RAW)
    raw["itp_resale"]["brackets"][0]["rate"] = 0.09
    raw["new_build"]["iva_dwelling"] = 0.21
    cfg = TaxConfig.from_dict(raw)
    assert purchase_tax(resale(500_000), cfg).total == 45_000
    nb = purchase_tax(CalcInput(property_type=PropertyType.NEW, price=Decimal(400_000)), cfg)
    assert nb.iva == 84_000


def test_input_roundtrip():
    inp = resale(
        450_000,
        reference_value=Decimal(460_000),
        mortgage_share=Decimal("0.6"),
        tax_status=TaxStatus.EU,
        family=Family.LARGE,
        children=4,
    )
    assert CalcInput.from_dict(inp.to_dict()) == inp


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("450000", 450_000),
        ("450 000", 450_000),
        ("450 000 €", 450_000),
        ("450k", 450_000),
        ("450К", 450_000),
        ("450 тыс", 450_000),
        ("450.000", 450_000),
        ("1.250.000", 1_250_000),
        ("1,2 млн", 1_200_000),
        ("1.5m", 1_500_000),
        ("€ 300000", 300_000),
        ("1500", 1_500),
        ("1 500,50", Decimal("1500.50")),
    ],
)
def test_parse_amount_ok(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["abc", "", "0", "-5", "45k0", "1.2.3k", "12,3456"])
def test_parse_amount_errors(text):
    with pytest.raises(AmountParseError):
        parse_amount(text)


def test_parse_percent():
    assert parse_percent("70") == Decimal("0.7")
    assert parse_percent("65 %") == Decimal("0.65")
    for bad in ("0", "150", "abc"):
        with pytest.raises(AmountParseError):
            parse_percent(bad)
