"""Fake Catastro HTTP transport (real recorded responses) and a scripted bot dialog."""

from __future__ import annotations

from pathlib import Path

import httpx
from aiogram.methods import EditMessageText, SendMessage

from tests.helpers import cb_update, feed, msg_update

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
