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
