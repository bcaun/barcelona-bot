from datetime import date

import pytest

from app.core.cadastre import (
    RcError,
    control_letters,
    looks_like_rc,
    normalize_rc,
    parse_rc,
    regulation_flags,
)

OFFICIAL = "9872023VH5797S0001WX"  # example from the Catastro OVC documentation


def test_official_example_control_letters():
    assert control_letters(OFFICIAL[:18]) == "WX"


@pytest.mark.parametrize("text", [OFFICIAL, "9872023 VH5797S 0001 WX", "9872023vh5797s0001wx"])
def test_parse_full_rc(text):
    p = parse_rc(text)
    assert (p.rc, p.is_parcel, p.parcel) == (OFFICIAL, False, "9872023VH5797S")


def test_parse_parcel_rc():
    p = parse_rc("9872023VH5797S")
    assert p.is_parcel and p.rc == "9872023VH5797S"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("9872023VH5797S0001WA", "control"),
        ("12345", "format"),
        ("9872023VH5797S00X1WX", "format"),
        ("", "format"),
    ],
)
def test_parse_rc_errors(text, reason):
    with pytest.raises(RcError) as e:
        parse_rc(text)
    assert e.value.reason == reason


def test_normalize_and_looks_like():
    assert normalize_rc(" 9872023-VH5797S ") == "9872023VH5797S"
    assert looks_like_rc(OFFICIAL) and not looks_like_rc("Carrer de Mallorca 401")


def test_regulation_flags():
    kw = {
        "stressed": {"barcelona"},
        "tourist_ban": {"barcelona"},
        "ite_age_years": 45,
        "today": date(2026, 11, 5),
    }
    assert regulation_flags("Barcelona", 1970, **kw) == [
        "stressed_market",
        "tourist_licence_barcelona",
        "ite_check",
    ]
    assert regulation_flags("Barcelona", 2000, **kw) == [
        "stressed_market",
        "tourist_licence_barcelona",
    ]
    assert regulation_flags("Girona", 1981, **kw) == ["ite_check"]
    assert regulation_flags("Girona", None, **kw) == []


# ---------------------------------------------------------------- parsing (real fixture)
import json  # noqa: E402
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402

from app.core.cadastre import (  # noqa: E402
    Property,
    errors,
    parse_dnp,
    unwrap,
)

FIX = Path(__file__).parent / "fixtures" / "catastro"


def _load(name):
    return unwrap(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_parse_real_single_property():
    p = parse_dnp(_load("dnprc_single.json"))
    assert isinstance(p, Property)
    assert p.rc == "2749704YJ0624N0001DI"
    assert p.address == "CL GUAYANA-MOJONERA 3 46388 GODELLETA (VALENCIA)"
    assert (p.use, p.area_m2, p.year_built, p.share_pct) == (
        "Residencial",
        94,
        1976,
        Decimal("100.000000"),
    )
    assert (p.municipality, p.province, p.kind) == ("GODELLETA", "VALENCIA", "UR")
    assert p.map_url.startswith("https://www1.sedecatastro.gob.es/Cartografia/mapa.aspx")
    assert Property.from_dict(p.to_dict()) == p
    assert errors(_load("dnprc_single.json")) == []


def test_filter_units():
    from app.core.cadastre import filter_units

    labels = [
        "CL X 1 Pl 01 Pt 01 · 50 m²",
        "CL X 1 Pl 01 Pt 02",
        "CL X 1 Esc 2 Pl 03 Pt 1",
        "CL X 1 Pl 00 Pt IN",
    ]
    assert filter_units(labels, "1 2") == [1]
    assert filter_units(labels, "3 1") == [2]
    assert filter_units(labels, "0 in") == [3]
    assert filter_units(labels, "01") == [0, 1]
    assert filter_units(labels, "3 1 2") == [2]
    assert filter_units(labels, "") == []


# ---------------------------------------------------------------- checklist
from app.core.cadastre import CheckRules, Level, checklist, flag_summary, parse_area  # noqa: E402

RULES = CheckRules(
    residential_uses=frozenset({"residencial"}),
    area_warn_pct=Decimal("0.10"),
    ite_age_years=45,
    stressed=frozenset({"barcelona"}),
    tourist_ban=frozenset({"barcelona"}),
)


def _prop(**kw):
    base = dict(
        rc="X",
        address="A",
        municipality="BARCELONA",
        province="BARCELONA",
        use="Residencial",
        area_m2=92,
        year_built=2013,
        share_pct=None,
        kind="UR",
        map_url=None,
    )
    base.update(kw)
    return Property(**base)


def test_checklist_residential_new_building_area_bigger():
    items = checklist(_prop(), 105, RULES, today=date(2026, 11, 6))
    got = [(i.level, i.key) for i in items]
    assert got == [
        (Level.OK, "use_ok"),
        (Level.WARN, "area_bigger"),
        (Level.OK, "ite_not_yet"),
        (Level.BAD, "tourist_banned"),
        (Level.WARN, "rent_capped"),
    ]
    assert items[1].params == {"listing": 105, "cadastre": 92, "diff": 14}
    assert flag_summary(items) == ["area_bigger", "tourist_banned", "rent_capped"]


def test_checklist_commercial_old_outside_barcelona():
    items = checklist(
        _prop(use="Comercial", year_built=1969, municipality="GIRONA"),
        None,
        RULES,
        today=date(2026, 11, 6),
    )
    assert [(i.level, i.key) for i in items] == [
        (Level.BAD, "use_not_residential"),
        (Level.INFO, "area_cadastre"),
        (Level.WARN, "ite_needed"),
    ]


@pytest.mark.parametrize(
    ("listing", "key"),
    [(100, "area_match"), (80, "area_smaller"), (101, "area_match"), (102, "area_bigger")],
)
def test_checklist_area_thresholds(listing, key):
    items = checklist(_prop(), listing, RULES)
    assert items[1].key == key


@pytest.mark.parametrize(
    ("text", "v"),
    [
        ("92", 92),
        ("92 m2", 92),
        ("92 м²", 92),
        ("92,5", 93),
        ("abc", None),
        ("3", None),
        ("105 кв.м", 105),
    ],
)
def test_parse_area(text, v):
    assert parse_area(text) == v
