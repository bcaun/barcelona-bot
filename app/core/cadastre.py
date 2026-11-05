"""Cadastral reference (referencia catastral, RC) validation and regulation flags.

Pure functions, no network. RC formats:
- 14 chars: parcel (7 + 7), e.g. 9872023VH5797S
- 20 chars: property = parcel (14) + 4-digit sequence + 2 control letters, e.g. ...0001WX
Control letters algorithm (Dirección General del Catastro), verified on the official example
9872023VH5797S0001WX from the OVC web services documentation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_WEIGHTS = (13, 15, 12, 5, 4, 17, 9, 21, 3, 7, 1)
_CONTROL = "MQWERTYUIOPASDFGHJKLBZX"
_ALPHABET = "ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
_PARCEL_RE = re.compile(r"[0-9A-ZÑ]{14}")
_FULL_RE = re.compile(r"[0-9A-ZÑ]{14}\d{4}[A-ZÑ]{2}")


class RcError(ValueError):
    """Invalid RC. `reason` is an i18n suffix: format | control."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_rc(text: str) -> str:
    return re.sub(r"[\s\-./]", "", (text or "").upper())


def _value(ch: str) -> int:
    return int(ch) if ch.isdigit() else _ALPHABET.index(ch) + 1


def control_letters(rc18: str) -> str:
    """Two control letters for the first 18 characters of a 20-char RC."""
    parts = (rc18[0:7] + rc18[14:18], rc18[7:14] + rc18[14:18])
    return "".join(
        _CONTROL[sum(_value(c) * w for c, w in zip(p, _WEIGHTS, strict=True)) % 23] for p in parts
    )


@dataclass(frozen=True)
class ParsedRc:
    rc: str
    is_parcel: bool  # 14 chars -> list properties on the parcel

    @property
    def parcel(self) -> str:
        return self.rc[:14]


def parse_rc(text: str) -> ParsedRc:
    rc = normalize_rc(text)
    if len(rc) == 14 and _PARCEL_RE.fullmatch(rc):
        return ParsedRc(rc, True)
    if len(rc) == 20 and _FULL_RE.fullmatch(rc):
        if control_letters(rc[:18]) != rc[18:]:
            raise RcError("control")
        return ParsedRc(rc, False)
    raise RcError("format")


def looks_like_rc(text: str) -> bool:
    rc = normalize_rc(text)
    return (
        len(rc) in (14, 20)
        and bool(re.fullmatch(r"[0-9A-ZÑ]+", rc))
        and any(c.isdigit() for c in rc)
    )


# ---------------------------------------------------------------- regulation flags


def regulation_flags(
    municipality: str,
    year_built: int | None,
    *,
    stressed: set[str],
    tourist_ban: set[str],
    ite_age_years: int,
    today: date | None = None,
) -> list[str]:
    """Keys of regulation.yaml texts that apply to a property."""
    m = municipality.strip().lower()
    flags: list[str] = []
    if m in stressed:
        flags.append("stressed_market")
    if m in tourist_ban:
        flags.append("tourist_licence_barcelona")
    if year_built and (today or date.today()).year - year_built >= ite_age_years:
        flags.append("ite_check")
    return flags


# ---------------------------------------------------------------- Catastro response parsing
# JSON from COVCCallejero.svc/json mirrors the XML schema of Webservices_Libres.pdf:
# {"<method>Result": {"control": {...}, "bico" | "lrcdnp" | "lerr" | ...}}
# Verified on a real Consulta_DNPRC response (tests/fixtures/catastro/dnprc_single.json).


def unwrap(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop the single '<Method>Result' wrapper, whatever the method is called."""
    if isinstance(raw, dict) and len(raw) == 1:
        (value,) = raw.values()
        if isinstance(value, dict):
            return value
    return raw


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def errors(body: dict[str, Any]) -> list[tuple[str, str]]:
    """[(code, description)] from 'lerr' (list or single 'err')."""
    out = []
    lerr = body.get("lerr")
    items = as_list(lerr.get("err") if isinstance(lerr, dict) else lerr)
    for e in items:
        if isinstance(e, dict):
            out.append((str(e.get("cod", "")), str(e.get("des", ""))))
    return out


def rc_from(d: dict[str, Any] | None) -> str:
    if not d:
        return ""
    return "".join(str(d.get(k, "")) for k in ("pc1", "pc2", "car", "cc1", "cc2"))


def _int(v: Any) -> int | None:
    """'94' -> 94, '2.259' -> 2259 (Catastro uses '.' as thousands separator in lists)."""
    s = str(v).strip() if v is not None else ""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return int(s)
    except ValueError:
        return None


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v).replace(",", ".")) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _lourb(dt: dict[str, Any]) -> dict[str, Any]:
    locs = dt.get("locs") or {}
    lous = locs.get("lous") or {}
    return lous.get("lourb") or {}


def unit_label(dt: dict[str, Any] | None) -> str:
    """'CL MALLORCA 401 Esc 1 Pl 02 Pt 1' from a 'dt' block."""
    if not dt:
        return ""
    lourb = _lourb(dt)
    d = lourb.get("dir") or {}
    li = lourb.get("loint") or {}
    parts = [str(d.get(k, "")).strip() for k in ("tv", "nv", "pnp", "plp")]
    for key, label in (("bq", "Bl"), ("es", "Esc"), ("pt", "Pl"), ("pu", "Pt")):
        if li.get(key):
            parts.append(f"{label} {li[key]}")
    return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class PropertyRef:
    rc: str
    label: str
    use: str = ""
    area_m2: int | None = None

    @property
    def button_text(self) -> str:
        area = f" · {self.area_m2} m²" if self.area_m2 else ""
        return f"{self.label}{area}"


def _norm_token(tok: str) -> str:
    tok = tok.strip().upper()
    return tok.lstrip("0") or "0" if tok.isdigit() else tok


def filter_units(labels: list[str], query: str) -> list[int]:
    """Indexes of labels matching '<floor> [<door> [<stair>]]', e.g. '1 2' -> floor 1, door 2.

    Compared to the label's 'Pl'/'Pt'/'Esc' parts with leading zeros ignored."""
    tokens = [_norm_token(t) for t in re.split(r"[\s,.;/-]+", query) if t.strip()]
    if not tokens:
        return []
    keys = ("Pl", "Pt", "Esc")[: len(tokens)]
    out = []
    for i, label in enumerate(labels):
        parts = {k: _norm_token(v) for k, v in re.findall(r"\b(Bl|Esc|Pl|Pt) (\S+)", label)}
        if all(parts.get(k) == tok for k, tok in zip(keys, tokens, strict=False)):
            out.append(i)
    return out


@dataclass(frozen=True)
class Property:
    rc: str
    address: str
    municipality: str
    province: str
    use: str
    area_m2: int | None
    year_built: int | None
    share_pct: Decimal | None
    kind: str  # UR / RU
    map_url: str | None
    province_code: str = ""
    municipality_code: str = ""  # DGC municipality code (cmc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rc": self.rc, "address": self.address, "municipality": self.municipality,
            "province": self.province, "use": self.use, "area_m2": self.area_m2,
            "year_built": self.year_built,
            "share_pct": str(self.share_pct) if self.share_pct is not None else None,
            "kind": self.kind, "map_url": self.map_url, "province_code": self.province_code,
            "municipality_code": self.municipality_code,
        }  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Property:
        return cls(**{**d, "share_pct": _dec(d.get("share_pct"))})


def parse_dnp(body: dict[str, Any]) -> Property | list[PropertyRef]:
    """Consulta_DNPRC / Consulta_DNPLOC body (already unwrapped)."""
    bico = body.get("bico")
    if bico:
        bi = bico.get("bi") or {}
        dt = bi.get("dt") or {}
        debi = bi.get("debi") or {}
        finca = bico.get("finca") or {}
        loine = dt.get("loine") or {}
        return Property(
            rc=rc_from((bi.get("idbi") or {}).get("rc")),
            address=str(bi.get("ldt") or finca.get("ldt") or "").strip(),
            municipality=str(dt.get("nm", "")),
            province=str(dt.get("np", "")),
            use=str(debi.get("luso", "")),
            area_m2=_int(debi.get("sfc")),
            year_built=_int(debi.get("ant")),
            share_pct=_dec(debi.get("cpt")),
            kind=str((bi.get("idbi") or {}).get("cn", "")),
            map_url=((finca.get("infgraf") or {}).get("igraf")) or None,
            province_code=str(loine.get("cp", "")),
            municipality_code=str(dt.get("cmc", "")),
        )
    lrc = body.get("lrcdnp")
    items = as_list(lrc.get("rcdnp") if isinstance(lrc, dict) else lrc)
    return [
        PropertyRef(
            rc_from(i.get("rc")), unit_label(i.get("dt")),
            str((i.get("debi") or {}).get("luso", "")), _int((i.get("debi") or {}).get("sfc")),
        )
        for i in items if i
    ]  # fmt: skip


@dataclass(frozen=True)
class ParcelRef:
    parcel_rc: str
    address: str


def parse_coords(body: dict[str, Any]) -> list[ParcelRef]:
    """Consulta_RCCOOR body -> parcel references (14 chars)."""
    coords = body.get("coordenadas")
    items = as_list(coords.get("coord") if isinstance(coords, dict) else coords)
    out = []
    for c in items:
        pc = (c or {}).get("pc") or {}
        rc = f"{pc.get('pc1', '')}{pc.get('pc2', '')}"
        if len(rc) == 14:
            out.append(ParcelRef(rc, str(c.get("ldt", "")).strip()))
    return out


@dataclass(frozen=True)
class Street:
    code: str
    type: str  # CL, AV, PZ ...
    name: str

    @property
    def label(self) -> str:
        return f"{self.type} {self.name}".strip()


def parse_streets(body: dict[str, Any]) -> list[Street]:
    """ConsultaVia body -> streets."""
    cal = body.get("callejero")
    items = as_list(cal.get("calle") if isinstance(cal, dict) else cal)
    out = []
    for c in items:
        d = (c or {}).get("dir") or {}
        if d.get("nv"):
            out.append(Street(str(d.get("cv", "")), str(d.get("tv", "")), str(d["nv"])))
    return out
