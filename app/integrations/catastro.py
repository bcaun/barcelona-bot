"""Client for Catastro open services («datos catastrales no protegidos»).

Docs: https://www.catastro.hacienda.gob.es/ws/Webservices_Libres.pdf (not WCFservices_Consulta.pdf,
which describes the certificate-protected services). JSON endpoints under
<base_url>/COVCCallejero.svc/json/... and <base_url>/COVCCoordenadas.svc/json/...

Self-check on the server against the real service:
    docker compose exec bot python -m app.integrations.catastro
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CatastroSettings
from app.core import cadastre as cd
from app.core.models import CatastroCache, utcnow

log = structlog.get_logger(__name__)

# Codes from Annex I meaning «nothing found / wrong input» (not a service failure)
NOT_FOUND_CODES = {"3", "4", "5", "7", "9", "12", "16", "22", "33", "43"}


class CatastroUnavailable(Exception):
    """Network error, timeout, 5xx, HTML error page or server-side error."""


@dataclass
class CatastroNotFound(Exception):
    code: str
    description: str
    nearby_numbers: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.code}: {self.description}"


class CatastroClient:
    def __init__(self, cfg: CatastroSettings, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        self._transport = transport

    async def _get(self, service: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.cfg.base_url}/{service}.svc/json/{method}"
        last: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            if attempt:
                await asyncio.sleep(self.cfg.retry_pause_seconds)
            try:
                async with httpx.AsyncClient(
                    timeout=self.cfg.timeout_seconds,
                    headers={"User-Agent": self.cfg.user_agent, "Accept": "application/json"},
                    transport=self._transport,
                ) as client:
                    resp = await client.get(url, params=params)
                if resp.status_code >= 500:
                    raise CatastroUnavailable(f"HTTP {resp.status_code}")
                text = resp.text.lstrip()
                if not text.startswith("{"):  # HTML «No se puede procesar su petición»
                    raise CatastroUnavailable("non-JSON response")
                body = cd.unwrap(json.loads(text))
            except (httpx.HTTPError, CatastroUnavailable, json.JSONDecodeError) as e:
                last = e
                log.warning(
                    "catastro_request_failed",
                    method=method,
                    attempt=attempt + 1,
                    error=type(e).__name__,
                )
                continue
            errs = cd.errors(body)
            if errs:
                code, desc = errs[0]
                if code in NOT_FOUND_CODES:
                    raise CatastroNotFound(code, desc, _nearby_numbers(body))
                log.warning("catastro_error", method=method, code=code, description=desc)
                raise CatastroUnavailable(f"{code}: {desc}")
            return body
        raise CatastroUnavailable(str(last) if last else "unknown")

    async def dnp_by_rc(self, rc: str) -> dict[str, Any]:
        return await self._get("COVCCallejero", "Consulta_DNPRC", {"RefCat": rc})

    async def dnp_by_address(
        self, province: str, municipality: str, street_type: str, street: str, number: str
    ) -> dict[str, Any]:
        return await self._get("COVCCallejero", "Consulta_DNPLOC", {
            "Provincia": province, "Municipio": municipality, "TipoVia": street_type,
            "NomVia": street, "Numero": number,
        })  # fmt: skip

    async def streets(self, province: str, municipality: str, name: str) -> dict[str, Any]:
        return await self._get("COVCCallejero", "ConsultaVia", {
            "Provincia": province, "Municipio": municipality, "TipoVia": "", "NomVia": name,
        })  # fmt: skip

    async def rc_by_coords(self, lat: float, lon: float) -> dict[str, Any]:
        # EPSG:4326: X = longitude, Y = latitude (Webservices_Libres.pdf, 2.2.1)
        return await self._get("COVCCoordenadas", "Consulta_RCCOOR", {
            "SRS": "EPSG:4326", "Coordenada_X": f"{lon:.6f}", "Coordenada_Y": f"{lat:.6f}",
        })  # fmt: skip


def _nearby_numbers(body: dict[str, Any]) -> list[str]:
    num = body.get("numerero")
    items = cd.as_list(num.get("nump") if isinstance(num, dict) else num)
    return [str((i.get("num") or {}).get("pnp", "")) for i in items if isinstance(i, dict)]


class CatastroService:
    """Parsed results + DB cache by RC (cache_days)."""

    def __init__(self, client: CatastroClient, cfg: CatastroSettings):
        self.client = client
        self.cfg = cfg

    async def _cached(self, session: AsyncSession, key: str) -> dict[str, Any] | None:
        row = await session.get(CatastroCache, key)
        if row is None:
            return None
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - fetched > timedelta(days=self.cfg.cache_days):
            return None
        return row.payload

    async def _store(self, session: AsyncSession, key: str, payload: dict[str, Any]) -> None:
        row = await session.get(CatastroCache, key)
        if row is None:
            session.add(CatastroCache(key=key, payload=payload, fetched_at=utcnow()))
        else:
            row.payload, row.fetched_at = payload, utcnow()

    async def by_rc(self, session: AsyncSession, rc: str) -> cd.Property | list[cd.PropertyRef]:
        key = f"dnprc:{rc}"
        body = await self._cached(session, key)
        if body is None:
            body = await self.client.dnp_by_rc(rc)
            await self._store(session, key, body)
        return cd.parse_dnp(body)

    async def by_address(
        self, province: str, municipality: str, street: cd.Street, number: str
    ) -> cd.Property | list[cd.PropertyRef]:
        body = await self.client.dnp_by_address(
            province, municipality, street.type, street.name, number
        )
        return cd.parse_dnp(body)

    async def streets(self, province: str, municipality: str, name: str) -> list[cd.Street]:
        return cd.parse_streets(await self.client.streets(province, municipality, name))

    async def parcels_at(self, lat: float, lon: float) -> list[cd.ParcelRef]:
        return cd.parse_coords(await self.client.rc_by_coords(lat, lon))


def card_url(cfg: CatastroSettings, p: cd.Property) -> str | None:
    if not cfg.card_url_template:
        return None
    return cfg.card_url_template.format(
        rc=p.rc, province_code=p.province_code, municipality_code=p.municipality_code
    )


# ---------------------------------------------------------------- self-check (real requests)

SELFTEST_RC = "2749704YJ0624N0001DI"  # example from Webservices_Libres.pdf
SELFTEST_POINT = (41.4036, 2.1744)  # Barcelona, Sagrada Família block
SELFTEST_ADDRESSES = [("MALLORCA", "CL", "401"), ("GRACIA", "PG", "43"), ("DIAGONAL", "AV", "640")]


async def selftest(cfg: CatastroSettings, raw: bool = True) -> int:  # pragma: no cover
    """Prints results of real requests; returns number of failed checks."""
    client = CatastroClient(cfg)
    failed = 0

    def show(title: str, ok: bool, detail: str, body: Any = None) -> None:
        nonlocal failed
        failed += not ok
        print(f"[{'OK' if ok else 'FAIL'}] {title}: {detail}")
        if raw and body is not None:
            print("    raw:", json.dumps(body, ensure_ascii=False)[:1200])

    async def run(title: str, coro):  # noqa: ANN001, ANN202
        try:
            return await coro
        except CatastroNotFound as e:
            show(title, False, f"not found {e}")
        except CatastroUnavailable as e:
            show(title, False, f"unavailable {e}")
        return None

    body = await run("DNPRC", client.dnp_by_rc(SELFTEST_RC))
    if body is not None:
        p = cd.parse_dnp(body)
        show("DNPRC", isinstance(p, cd.Property), repr(p)[:300])
        if isinstance(p, cd.Property) and (url := card_url(cfg, p)):
            try:
                async with httpx.AsyncClient(
                    timeout=cfg.timeout_seconds, headers={"User-Agent": cfg.user_agent}
                ) as c:
                    r = await c.get(url, follow_redirects=True)
                show(
                    "card_url",
                    r.status_code == 200 and "catastr" in r.text.lower(),
                    f"HTTP {r.status_code} {url}",
                )
            except httpx.HTTPError as e:
                show("card_url", False, type(e).__name__)

    lat, lon = SELFTEST_POINT
    body = await run("RCCOOR", client.rc_by_coords(lat, lon))
    if body is not None:
        parcels = cd.parse_coords(body)
        show("RCCOOR", bool(parcels), repr(parcels)[:300], body)
        if parcels:
            b2 = await run("DNPRC parcel", client.dnp_by_rc(parcels[0].parcel_rc))
            if b2 is not None:
                res = cd.parse_dnp(b2)
                n = len(res) if isinstance(res, list) else 1
                show("DNPRC parcel", n > 0, f"{n} item(s): {repr(res)[:300]}", b2)

    prov, mun = cfg.default_province, cfg.default_municipality
    for name, street_type, number in SELFTEST_ADDRESSES:
        body = await run(f"ConsultaVia {name}", client.streets(prov, mun, name))
        if body is None:
            continue
        streets = cd.parse_streets(body)
        show(f"ConsultaVia {name}", bool(streets), repr(streets[:5])[:300], body)
        street = next(
            (s for s in streets if s.type == street_type), streets[0] if streets else None
        )
        if street is None:
            continue
        b3 = await run(
            f"DNPLOC {street.label} {number}",
            client.dnp_by_address(prov, mun, street.type, street.name, number),
        )
        if b3 is not None:
            res = cd.parse_dnp(b3)
            n = len(res) if isinstance(res, list) else 1
            show(f"DNPLOC {street.label} {number}", n > 0, f"{n} item(s): {repr(res)[:300]}", b3)
    print(f"\nFAILED: {failed}")
    return failed


if __name__ == "__main__":  # pragma: no cover
    import sys

    from app.config import get_settings

    sys.exit(
        1 if asyncio.run(selftest(get_settings().catastro, raw="--quiet" not in sys.argv)) else 0
    )
