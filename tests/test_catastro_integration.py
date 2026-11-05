"""Real requests to Catastro. Run separately: pytest -m integration
(needs network access to ovc.catastro.meh.es; cloud sandboxes are usually blocked)."""

import pytest

from app.config import get_settings
from app.core import cadastre as cd
from app.integrations.catastro import SELFTEST_RC, CatastroClient

pytestmark = pytest.mark.integration


async def test_real_dnprc():
    body = await CatastroClient(get_settings().catastro).dnp_by_rc(SELFTEST_RC)
    p = cd.parse_dnp(body)
    assert isinstance(p, cd.Property) and p.rc == SELFTEST_RC and p.area_m2


async def test_real_coords_barcelona():
    body = await CatastroClient(get_settings().catastro).rc_by_coords(41.4036, 2.1744)
    assert cd.parse_coords(body)


async def test_real_street_and_address():
    cfg = get_settings().catastro
    c = CatastroClient(cfg)
    streets = cd.parse_streets(
        await c.streets(cfg.default_province, cfg.default_municipality, "MALLORCA")
    )
    street = next(s for s in streets if s.type == "CL")
    res = cd.parse_dnp(
        await c.dnp_by_address(
            cfg.default_province, cfg.default_municipality, street.type, street.name, "401"
        )
    )
    assert res
