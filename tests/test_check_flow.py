"""«Check a property» end-to-end with a fake Catastro HTTP transport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import select

from app.config import CatastroSettings
from app.core.cadastre import errors, parse_coords, parse_dnp, parse_streets, unwrap
from app.core.models import Lead
from app.integrations.catastro import CatastroClient, CatastroService
from tests.conftest import ADMIN_CHAT
from tests.helpers import cb_update, feed, msg_update, tg_user

FIX = Path(__file__).parent / "fixtures" / "catastro"
J = {p.stem: p.read_text(encoding="utf-8") for p in FIX.glob("*.json")}
LIST_PARCEL = "6922624DF2862B"  # real parcel, fixture trimmed to 12 units
UNIT = "6922624DF2862B0005QA"  # real unit: Barcelona, Comercial, 531 m², 1969
NOT_FOUND = {"r": {"lerr": [{"cod": "9", "des": "LA REFERENCIA CATASTRAL NO EXISTE"}]}}


def catastro_router(down: bool = False):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if down:
            return httpx.Response(200, text="<html>No se puede procesar su petici&oacute;n</html>")
        method = req.url.path.rsplit("/", 1)[-1]
        q = req.url.params
        if method == "Consulta_DNPRC":
            rc = q["RefCat"]
            fixture = {
                LIST_PARCEL: "real_dnprc_list",
                "1040501DF3814A": "real_dnprc_parcel_single",
                "2749704YJ0624N0001DI": "dnprc_single",
            }.get(rc, "real_dnprc_unit" if rc.startswith(LIST_PARCEL) else None)
            return (
                httpx.Response(200, text=J[fixture])
                if fixture
                else httpx.Response(200, json=NOT_FOUND)
            )
        if method == "Consulta_RCCOOR":
            return httpx.Response(200, text=J["real_rccoor"])
        if method == "ObtenerCallejero":
            name = (
                "real_callejero_mallorca"
                if q["NomVia"].upper() == "MALLORCA"
                else ("real_callejero_none")
            )
            return httpx.Response(200, text=J[name])
        if method == "Consulta_DNPLOC":
            name = "real_dnploc_single" if q["Numero"] == "406" else "real_dnploc_notfound"
            return httpx.Response(200, text=J[name])
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


@pytest.fixture
def make_dlg(dp, bot, fake_session):
    def make(down=False, uid=70, lang="ru"):
        transport, seen = catastro_router(down)
        cfg = CatastroSettings(
            retries=0, retry_pause_seconds=0, card_url_template="https://sede/?RefC={rc}"
        )
        dp["catastro"] = CatastroService(CatastroClient(cfg, transport), cfg)
        return Dialog(dp, bot, fake_session, tg_user(uid, lang)), seen

    return make


class Dialog:
    def __init__(self, dp, bot, fake, user):
        self.dp, self.bot, self.fake, self.user = dp, bot, fake, user

    def last(self):
        return [c for c in self.fake.calls if isinstance(c, SendMessage | EditMessageText)][-1]

    async def cb(self, data):
        await feed(self.dp, self.bot, cb_update(self.user, data))
        return self.last().text

    async def say(self, text=None, **kw):
        await feed(self.dp, self.bot, msg_update(self.user, text, **kw))
        return self.last().text

    def buttons(self):
        m = self.last().reply_markup
        return [b.callback_data for row in m.inline_keyboard for b in row]


def test_real_fixtures_parse():
    units = parse_dnp(unwrap(json.loads(J["real_dnprc_list"])))
    assert len(units) == 12 and units[0].area_m2 == 2259  # '2.259' -> 2259
    assert units[1].button_text == "CL SABINO ARANA 32 B Pl 01 Pt 01 · 531 m²"
    assert parse_coords(unwrap(json.loads(J["real_rccoor"])))[0].parcel_rc == "1040501DF3814A"
    assert [s.label for s in parse_streets(unwrap(json.loads(J["real_callejero_mallorca"])))] == [
        "PJ CIUTAT DE MALLORCA",
        "PS CIUTAT DE MALLORCA",
        "CL MALLORCA",
    ]
    assert errors(unwrap(json.loads(J["real_callejero_none"])))[0][0] == "10"


async def test_by_rc_card_with_flags(make_dlg):
    d, _ = make_dlg()
    assert "Как будем искать" in await d.cb("menu:check")
    assert "кадастровый номер" in await d.cb("k:m:rc")
    assert "Контрольные буквы" in await d.say("6922624DF2862B0005QB")
    assert "не похоже" in await d.say("hello")
    ask = await d.say(UNIT)
    assert "Объект найден: <b>CL SABINO ARANA 32(B) Pl:01 Pt:01</b>" in ask
    assert "Напишите площадь" in await d.say("abc")
    verdict = await d.say("600 м2")  # listing claims more than the cadastre's 531 m²
    assert f"RC <code>{UNIT}</code>" in verdict
    assert "❌ По кадастру это не жильё: торговое (Comercial)" in verdict
    assert "cédula de habitabilidad" in verdict
    assert "⚠️ Площадь: в объявлении 600 м², в кадастре 531 м² (+13%)" in verdict
    assert "⚠️ Здание 1969 года" in verdict
    assert "❌ Туристическая аренда невозможна" in verdict and "⚠️ Аренда ограничена" in verdict
    assert "не показывают собственника, ипотеки" in verdict
    assert d.buttons() == ["k:nota", "k:calc", "k:details", "k:consult", "k:m:again"]
    assert "25 €" in d.last().reply_markup.inline_keyboard[0][0].text
    card = await d.cb("k:details")
    assert f"RC: <code>{UNIT}</code>" in card and "531 м²" in card and "2,07%" in card
    assert f"https://sede/?RefC={UNIT}" in card and "mapa.aspx" in card
    assert "напряжённого рынка" in card and "ITE / IEE" in card


async def test_parcel_rc_lists_units_filter_and_pick(make_dlg):
    d, _ = make_dlg()
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    text = await d.say(LIST_PARCEL)
    assert "несколько объектов (12)" in text and "этаж и дверь" in text
    assert d.buttons()[:2] == ["k:u:0", "k:u:1"]
    assert "объектов (1)" in await d.say("1 2")  # floor 01, door 02
    assert "Pl 01 Pt 02" in d.last().reply_markup.inline_keyboard[0][0].text
    assert "нет" in await d.say("9 9")
    assert "Объект найден" in await d.cb("k:u:0")
    assert "RC <code>6922624DF2862B" in await d.cb("k:area:skip")


async def test_unit_pagination(make_dlg, settings):
    settings.catastro.units_page_size = 5
    try:
        d, _ = make_dlg()
        await d.cb("menu:check")
        await d.cb("k:m:rc")
        await d.say(LIST_PARCEL)
        assert d.buttons()[5] == "k:p:1"
        await d.cb("k:p:1")
        kb_ = d.fake.calls[-1].reply_markup.inline_keyboard
        assert kb_[0][0].callback_data == "k:u:5" and kb_[5][0].callback_data == "k:p:0"
    finally:
        settings.catastro.units_page_size = 8


async def test_by_address_full_path(make_dlg):
    d, seen = make_dlg()
    await d.cb("menu:check")
    assert "Barcelona" in await d.cb("k:m:addr")
    assert "Не нашёл" in await d.say("Nonexistent")
    assert "Выберите улицу" in await d.say("Carrer de Mallorca")
    assert seen[-1].url.params["NomVia"] == "Mallorca"
    assert "CL MALLORCA" in await d.cb("k:s:2")
    assert "Есть номера: 406" in await d.say("401")
    await d.say("406")
    card = await d.cb("k:area:skip")
    loc = [r for r in seen if r.url.path.endswith("Consulta_DNPLOC")][-1].url.params
    assert (loc["Provincia"], loc["Municipio"], loc["Sigla"], loc["Calle"], loc["Numero"]) == (
        "BARCELONA",
        "BARCELONA",
        "CL",
        "MALLORCA",
        "406",
    )
    assert "CL MALLORCA 406" in card and "ℹ️ Площадь по кадастру: 115 м²" in card
    assert "Здание 1987 года" in card


async def test_change_municipality(make_dlg):
    d, seen = make_dlg()
    await d.cb("menu:check")
    await d.cb("k:m:addr")
    assert "муниципалитета" in await d.cb("k:mun")
    assert "Badalona" in await d.say("Badalona")
    await d.say("Mallorca")
    assert seen[-1].url.params["Municipio"] == "BADALONA"


async def test_by_location(make_dlg):
    from aiogram.types import Location

    d, seen = make_dlg()
    await d.cb("menu:check")
    await d.cb("k:m:geo")
    assert "геоточку" in d.last().text
    await d.say(location=Location(latitude=41.4036, longitude=2.1744))
    assert "CL MARINA 253" in d.last().text  # parcel without division -> straight to the unit
    p = [r for r in seen if "RCCOOR" in r.url.path][0].url.params
    assert (p["CoorX"], p["CoorY"], p["SRS"]) == ("2.174400", "41.403600", "EPSG:4326")


async def test_service_down_message_and_no_crash(make_dlg):
    d, _ = make_dlg(down=True)
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    assert "Сервис кадастра сейчас недоступен" in await d.say(UNIT)
    await d.cb("k:m:addr")  # bot still responds
    assert "Сервис кадастра сейчас недоступен" in await d.say("Mallorca")


async def test_not_found(make_dlg):
    d, _ = make_dlg()
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    assert "ничего не нашлось" in await d.say("2749704YJ0624N0002FO")  # valid, not in cadastre


async def test_nota_simple_lead_reaches_admin(make_dlg, session_factory):
    d, _ = make_dlg()
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    await d.say(UNIT)
    await d.say("600")
    assert "согласие" in await d.cb("k:nota")
    await d.cb("contact:consent")
    await d.say("💬 Только Telegram, без телефона")
    cards = [c for c in d.fake.sent() if c.chat_id == ADMIN_CHAT]
    assert len(cards) == 1 and "Nota simple" in cards[0].text and UNIT in cards[0].text
    async with session_factory() as s:
        lead = (await s.scalars(select(Lead))).one()
        assert lead.lead_type == "nota_simple" and lead.data["RC"] == UNIT
        assert lead.data["Площадь в объявлении, м²"] == 600 and lead.data["Цена услуги"] == "25 €"
        assert "не жильё" in lead.data["Флаги"] and "площадь завышена" in lead.data["Флаги"]


async def test_object_check_lead_and_calc_button(make_dlg):
    d, _ = make_dlg()
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    await d.say("2749704YJ0624N0001DI")
    await d.cb("k:area:skip")
    assert "Цена покупки" in await d.cb("k:calc")  # «resale» pre-selected
    assert "Какой объект" in await d.cb("c:back")
    assert "согласие" in await d.cb("k:consult")  # works from DB event after FSM reset


async def test_english_card(make_dlg):
    d, _ = make_dlg(uid=71, lang="en")
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    await d.say(UNIT)
    card = await d.cb("k:area:skip")
    assert "Per the cadastre this is not a dwelling" in card and "Pre-purchase check" in card
    assert "Built area: 531 m²" in await d.cb("k:details")


async def test_stats_show_checks_and_flags(make_dlg, bot, fake_session):
    from tests.conftest import ADMIN_ID

    d, _ = make_dlg()
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    await d.say(UNIT)
    await d.say("600")
    admin = Dialog(d.dp, bot, fake_session, tg_user(ADMIN_ID, "ru", "boss"))
    stats = await admin.say("/stats")
    assert "Проверок объектов: 1" in stats
    assert "не жильё: 1" in stats and "площадь завышена: 1" in stats
