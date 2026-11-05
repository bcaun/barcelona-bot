"""Catastro client against a fake HTTP transport (no network)."""

import json
from pathlib import Path

import httpx
import pytest

from app.config import CatastroSettings
from app.core.cadastre import Property
from app.integrations.catastro import (
    CatastroClient,
    CatastroNotFound,
    CatastroService,
    CatastroUnavailable,
    card_url,
)

FIX = Path(__file__).parent / "fixtures" / "catastro"
SINGLE = (FIX / "dnprc_single.json").read_text(encoding="utf-8")
HTML_ERR = "<html><body>No se puede procesar su petici&oacute;n.</body></html>"
CFG = CatastroSettings(
    retries=2,
    retry_pause_seconds=0,
    user_agent="barcelona-bot/test",
    card_url_template="https://x/?RefC={rc}&del={province_code}&mun={municipality_code}",
)


def transport(responses, seen=None):
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    return httpx.MockTransport(handler)


async def test_success_sends_user_agent_and_params():
    seen = []
    c = CatastroClient(CFG, transport([httpx.Response(200, text=SINGLE)], seen))
    body = await c.dnp_by_rc("2749704YJ0624N0001DI")
    assert "bico" in body
    req = seen[0]
    assert req.headers["User-Agent"] == "barcelona-bot/test"
    assert req.url.path.endswith("/COVCCallejero.svc/json/Consulta_DNPRC")
    assert req.url.params["RefCat"] == "2749704YJ0624N0001DI"


async def test_retries_html_then_succeeds():
    c = CatastroClient(
        CFG,
        transport(
            [
                httpx.Response(200, text=HTML_ERR),
                httpx.ConnectTimeout("t"),
                httpx.Response(200, text=SINGLE),
            ]
        ),
    )
    assert "bico" in await c.dnp_by_rc("x")


async def test_gives_up_after_retries():
    c = CatastroClient(CFG, transport([httpx.Response(503)] * 3))
    with pytest.raises(CatastroUnavailable):
        await c.dnp_by_rc("x")


async def test_not_found_error_code():
    body = {
        "consulta_dnprcResult": {
            "control": {"cuerr": 1},
            "lerr": [{"cod": "9", "des": "LA REFERENCIA CATASTRAL NO EXISTE"}],
        }
    }
    c = CatastroClient(CFG, transport([httpx.Response(200, json=body)]))
    with pytest.raises(CatastroNotFound) as e:
        await c.dnp_by_rc("x")
    assert e.value.code == "9"


async def test_coords_param_order():
    seen = []
    c = CatastroClient(CFG, transport([httpx.Response(200, json={"r": {"coordenadas": {}}})], seen))
    await c.rc_by_coords(41.4036, 2.1744)
    p = seen[0].url.params
    assert (p["SRS"], p["CoorX"], p["CoorY"]) == (
        "EPSG:4326",
        "2.174400",
        "41.403600",
    )


async def test_service_cache(session_factory):
    seen = []
    svc = CatastroService(
        CatastroClient(CFG, transport([httpx.Response(200, text=SINGLE)], seen)), CFG
    )
    async with session_factory() as s:
        p1 = await svc.by_rc(s, "2749704YJ0624N0001DI")
        await s.commit()
    async with session_factory() as s:
        p2 = await svc.by_rc(s, "2749704YJ0624N0001DI")  # from cache, no 2nd request
    assert p1 == p2 and isinstance(p1, Property) and len(seen) == 1
    assert card_url(CFG, p1) == "https://x/?RefC=2749704YJ0624N0001DI&del=46&mun=138"


def test_fixture_is_json():
    assert json.loads(SINGLE)


async def test_list_queries_get_longer_timeout():
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, text=SINGLE)

    cfg = CFG.model_copy(update={"timeout_seconds": 10, "list_timeout_seconds": 30})
    c = CatastroClient(cfg, httpx.MockTransport(handler))
    await c.dnp_by_rc("2749704YJ0624N0001DI")
    await c.dnp_by_rc("2749704YJ0624N")
    await c.dnp_by_address("B", "B", "CL", "X", "1")
    assert seen == [10, 30, 30]
