"""«Check a property» (SPEC 6): by RC, by address (street directory) or by location.

Catastro data -> card + regulation flags -> leads `nota_simple` / `object_check` or calculator.
"""

from __future__ import annotations

import html
import re
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.handlers.calc import start_calc
from app.bot.handlers.contact import start_lead_flow
from app.bot.handlers.start import send_menu
from app.config import CatastroSettings, Regulation, Settings, get_regulation
from app.core import cadastre as cd
from app.core import repo
from app.core.models import Event, User
from app.core.money import format_pct
from app.i18n import t
from app.integrations.catastro import (
    CatastroNotFound,
    CatastroService,
    CatastroUnavailable,
    card_url,
)

router = Router(name="check")
log = structlog.get_logger(__name__)

P = "k:"
CB_BACK = P + "back"
CB_MUNICIPALITY = P + "mun"
CB_NOTA = P + "nota"
CB_CONSULT = P + "consult"
CB_CALC = P + "calc"
MAX_STREETS = 10
CANCEL_TEXTS = {t(x, "common.cancel") for x in ("ru", "en")}


class CheckFlow(StatesGroup):
    rc = State()
    municipality = State()
    street = State()
    number = State()
    location = State()
    pick = State()  # choosing street / unit by buttons


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _nav(lang: str) -> list[InlineKeyboardButton]:
    return [_btn(t(lang, "check.back"), CB_BACK), _btn(t(lang, "common.cancel"), kb.CB.CANCEL)]


def _choose_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "check.by_rc"), P + "m:rc")],
        [_btn(t(lang, "check.by_address"), P + "m:addr")],
        [_btn(t(lang, "check.by_location"), P + "m:geo")],
        [_btn(t(lang, "check.back"), kb.CB.MENU)],
    ])  # fmt: skip


async def start_check(message: Message, state: FSMContext, lang: str, edit: bool = False) -> None:
    await state.clear()
    text, markup = t(lang, "check.choose"), _choose_markup(lang)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


# ---------------------------------------------------------------- rendering


def _use_label(use: str, lang: str) -> str:
    key = f"check.use.{use.strip().lower()}"
    tr = t(lang, key)
    return tr if tr != key else use


def render_card(p: cd.Property, lang: str, catastro_cfg: CatastroSettings, reg: Regulation) -> str:
    e = html.escape
    lines = [t(lang, "check.card.title"), "", t(lang, "check.card.rc", v=e(p.rc))]
    if p.address:
        lines.append(t(lang, "check.card.address", v=e(p.address)))
    if p.use:
        lines.append(t(lang, "check.card.use", v=e(_use_label(p.use, lang))))
    if p.area_m2:
        lines.append(t(lang, "check.card.area", v=p.area_m2))
    if p.year_built:
        lines.append(t(lang, "check.card.year", v=p.year_built))
    if p.share_pct is not None:
        lines.append(t(lang, "check.card.share", v=format_pct(p.share_pct / 100, lang, 4)))
    links = []
    if url := card_url(catastro_cfg, p):
        links.append(t(lang, "check.card.links", card=e(url, quote=True)))
    if p.map_url:
        links.append(t(lang, "check.card.map", map=e(p.map_url, quote=True)))
    if links:
        lines += [""] + links

    municipality = p.municipality.strip().lower()
    flags = cd.regulation_flags(
        municipality, p.year_built,
        stressed={m.lower() for m in reg.stressed_market_municipalities},
        tourist_ban={"barcelona"} if reg.text("tourist_licence_barcelona", lang) else set(),
        ite_age_years=reg.ite_age_threshold_years,
    )  # fmt: skip
    if flags:
        lines += ["", t(lang, "check.card.flags_title")]
        for f in flags:
            text = reg.text(f, lang).replace("{age}", str(reg.ite_age_threshold_years))
            if text:
                lines.append("⚠️ " + e(text))
            if f == "stressed_market" and reg.links.get("rent_index"):
                lines.append(t(lang, "check.card.rent_index", url=e(reg.links["rent_index"])))
    lines += ["", t(lang, "check.card.note")]
    return "\n".join(lines)


def _card_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "check.btn.nota"), CB_NOTA)],
        [_btn(t(lang, "check.btn.calc"), CB_CALC)],
        [_btn(t(lang, "check.btn.consult"), CB_CONSULT)],
        [_btn(t(lang, "check.back"), P + "m:again")],
    ])  # fmt: skip


def _units_markup(units: list[list[str]], page: int, size: int, lang: str) -> InlineKeyboardMarkup:
    start = page * size
    rows = [
        [_btn((label or rc)[:64], P + f"u:{start + i}")]
        for i, (rc, label) in enumerate(units[start : start + size])
    ]
    nav = []
    if page > 0:
        nav.append(_btn(t(lang, "check.prev"), P + f"p:{page - 1}"))
    if start + size < len(units):
        nav.append(_btn(t(lang, "check.next"), P + f"p:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(_nav(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- result handling


async def _show(
    target: Message,
    res: cd.Property | list[cd.PropertyRef],
    *,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    catastro: CatastroService,
    method: str,
) -> None:
    if isinstance(res, list):
        if not res:
            await target.answer(t(lang, "check.err.not_found"))
            return
        if len(res) == 1:
            res = await catastro.by_rc(session, res[0].rc)
            if isinstance(res, list):  # defensive: should be a single property now
                await target.answer(t(lang, "check.err.not_found"))
                return
        else:
            units = [[u.rc, u.button_text] for u in res]
            await state.set_state(CheckFlow.pick)
            await state.update_data(units=units, all_units=units, method=method)
            text = t(lang, "check.pick_unit", count=len(units))
            if len(units) > settings.catastro.units_page_size:
                text += "\n\n" + t(lang, "check.filter_hint")
            await target.answer(
                text, reply_markup=_units_markup(units, 0, settings.catastro.units_page_size, lang)
            )
            return
    await state.set_state(None)
    await state.update_data(property=res.to_dict())
    await repo.log_event(
        session,
        user.id,
        "check:done",
        {"method": method, "property": res.to_dict()},
        source=user.last_source or user.source,
    )
    await target.answer(
        render_card(res, lang, catastro.cfg, get_regulation()),
        reply_markup=_card_markup(lang),
        disable_web_page_preview=True,
    )


async def _safe(target: Message, lang: str, coro, nearby: bool = False):  # noqa: ANN001, ANN202
    """Run a Catastro call; on failure tell the user and return None."""
    try:
        return await coro
    except CatastroUnavailable:
        await target.answer(t(lang, "check.err.unavailable"), reply_markup=ReplyKeyboardRemove())
    except CatastroNotFound as e:
        if nearby and e.nearby_numbers:
            nums = ", ".join(n for n in e.nearby_numbers if n)
            await target.answer(
                t(lang, "check.err.number_not_found")
                + " "
                + t(lang, "check.err.nearby", numbers=html.escape(nums))
            )
        else:
            await target.answer(t(lang, "check.err.not_found"), reply_markup=ReplyKeyboardRemove())
    return None


async def _last_property(
    state: FSMContext, session: AsyncSession, user: User
) -> cd.Property | None:
    d = (await state.get_data()).get("property")
    if d is None:
        ev = await session.scalar(
            select(Event)
            .where(Event.user_id == user.id, Event.event_type == "check:done")
            .order_by(Event.id.desc())
            .limit(1)
        )
        d = (ev.payload or {}).get("property") if ev else None
    try:
        return cd.Property.from_dict(d) if d else None
    except (TypeError, ValueError):
        return None


def lead_data(p: cd.Property, lang: str) -> dict[str, Any]:
    d: dict[str, Any] = {t(lang, "check.lead.rc"): p.rc, t(lang, "check.lead.address"): p.address}
    if p.use:
        d[t(lang, "check.lead.use")] = p.use
    if p.area_m2:
        d[t(lang, "check.lead.area")] = p.area_m2
    if p.year_built:
        d[t(lang, "check.lead.year")] = p.year_built
    return d


# ---------------------------------------------------------------- entry & method choice


@router.callback_query(F.data == kb.CB.CHECK)
async def cb_start(cq: CallbackQuery, state: FSMContext, lang: str) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await start_check(cq.message, state, lang)


@router.message(Command("check"))
async def cmd_check(message: Message, state: FSMContext, lang: str) -> None:
    await start_check(message, state, lang)


@router.callback_query(F.data.in_({CB_BACK, P + "m:again"}))
async def cb_back(cq: CallbackQuery, state: FSMContext, lang: str) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await start_check(cq.message, state, lang, edit=cq.data == CB_BACK)


async def _ask_street(
    message: Message, state: FSMContext, lang: str, settings: Settings, edit: bool
) -> None:
    data = await state.get_data()
    municipality = data.get("municipality") or settings.catastro.default_municipality
    await state.set_state(CheckFlow.street)
    await state.update_data(municipality=municipality)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[_btn(t(lang, "check.change_municipality"), CB_MUNICIPALITY)], _nav(lang)]
    )
    text = t(lang, "check.ask_street", municipality=html.escape(municipality.title()))
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith(P + "m:"))
async def cb_method(cq: CallbackQuery, state: FSMContext, lang: str, settings: Settings) -> None:
    await cq.answer()
    if not isinstance(cq.message, Message):
        return
    method = (cq.data or "")[len(P + "m:") :]
    if method == "rc":
        await state.set_state(CheckFlow.rc)
        await cq.message.edit_text(
            t(lang, "check.ask_rc"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[_nav(lang)])
        )
    elif method == "addr":
        await _ask_street(cq.message, state, lang, settings, edit=True)
    elif method == "geo":
        await state.set_state(CheckFlow.location)
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.message.answer(t(lang, "check.ask_location"), reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=t(lang, "check.send_location"), request_location=True)],
                      [KeyboardButton(text=t(lang, "common.cancel"))]],
            resize_keyboard=True, one_time_keyboard=True,
        ))  # fmt: skip


# ---------------------------------------------------------------- by RC


@router.message(CheckFlow.rc, F.text & ~F.text.startswith("/"))
async def got_rc(
    message: Message,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    catastro: CatastroService,
) -> None:
    try:
        parsed = cd.parse_rc(message.text or "")
    except cd.RcError as e:
        await message.answer(t(lang, f"check.err.rc_{e.reason}"))
        return
    if parsed.is_parcel:  # a whole building can take ~20 s
        await message.answer(t(lang, "check.searching"))
    res = await _safe(message, lang, catastro.by_rc(session, parsed.rc))
    if res is not None:
        await _show(
            message,
            res,
            state=state,
            lang=lang,
            settings=settings,
            session=session,
            user=user,
            catastro=catastro,
            method="rc",
        )


# ---------------------------------------------------------------- by address


@router.callback_query(F.data == CB_MUNICIPALITY)
async def cb_municipality(cq: CallbackQuery, state: FSMContext, lang: str) -> None:
    await cq.answer()
    await state.set_state(CheckFlow.municipality)
    if isinstance(cq.message, Message):
        await cq.message.edit_text(
            t(lang, "check.ask_municipality"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_nav(lang)]),
        )


@router.message(CheckFlow.municipality, F.text & ~F.text.startswith("/"))
async def got_municipality(
    message: Message, state: FSMContext, lang: str, settings: Settings
) -> None:
    await state.update_data(municipality=(message.text or "").strip().upper()[:40])
    await _ask_street(message, state, lang, settings, edit=False)


@router.message(CheckFlow.street, F.text & ~F.text.startswith("/"))
async def got_street(
    message: Message, state: FSMContext, lang: str, settings: Settings, catastro: CatastroService
) -> None:
    data = await state.get_data()
    municipality = data.get("municipality") or settings.catastro.default_municipality
    name = re.sub(
        r"^(carrer|calle|avinguda|avenida|passeig|paseo|plaça|plaza)\s+(de\s+|del\s+|"
        r"d'|la\s+|les\s+)?",
        "",
        (message.text or "").strip(),
        flags=re.I,
    )[:25]
    try:
        streets = await catastro.streets(settings.catastro.default_province, municipality, name)
    except CatastroNotFound:
        streets = []
    except CatastroUnavailable:
        await message.answer(t(lang, "check.err.unavailable"))
        return
    if not streets:
        await message.answer(
            t(lang, "check.err.street_not_found", municipality=html.escape(municipality.title()))
        )
        return
    found = [[s.code, s.type, s.name] for s in streets[:MAX_STREETS]]
    await state.set_state(CheckFlow.pick)
    await state.update_data(streets=found)
    rows = [[_btn(f"{tp} {nm}"[:60], P + f"s:{i}")] for i, (_, tp, nm) in enumerate(found)]
    rows.append(_nav(lang))
    await message.answer(
        t(lang, "check.pick_street"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(CheckFlow.pick, F.data.startswith(P + "s:"))
async def cb_street(cq: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    streets = data.get("streets") or []
    try:
        code, tp, nm = streets[int((cq.data or "").split(":")[2])]
    except (ValueError, IndexError):
        await cq.answer(t(lang, "check.err.expired"), show_alert=True)
        return
    await cq.answer()
    await state.set_state(CheckFlow.number)
    await state.update_data(street=[code, tp, nm])
    if isinstance(cq.message, Message):
        await cq.message.edit_text(
            t(lang, "check.ask_number", street=html.escape(f"{tp} {nm}")),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_nav(lang)]),
        )


@router.message(CheckFlow.number, F.text & ~F.text.startswith("/"))
async def got_number(
    message: Message,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    catastro: CatastroService,
) -> None:
    m = re.match(r"\s*(\d{1,4})", message.text or "")
    if not m:
        await message.answer(t(lang, "check.err.number_format"))
        return
    data = await state.get_data()
    code, tp, nm = data["street"]
    municipality = data.get("municipality") or settings.catastro.default_municipality
    await message.answer(t(lang, "check.searching"))
    res = await _safe(message, lang, catastro.by_address(
        settings.catastro.default_province, municipality, cd.Street(code, tp, nm), m.group(1),
    ), nearby=True)  # fmt: skip
    if res is not None:
        await _show(
            message,
            res,
            state=state,
            lang=lang,
            settings=settings,
            session=session,
            user=user,
            catastro=catastro,
            method="address",
        )


# ---------------------------------------------------------------- by location


@router.message(CheckFlow.location, F.location)
async def got_location(
    message: Message,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    catastro: CatastroService,
) -> None:
    loc = message.location
    if loc is None:
        return
    await message.answer(t(lang, "check.searching"), reply_markup=ReplyKeyboardRemove())
    parcels = await _safe(message, lang, catastro.parcels_at(loc.latitude, loc.longitude))
    if parcels is None:
        return
    if not parcels:
        await message.answer(t(lang, "check.err.location_empty"))
        return
    res = await _safe(message, lang, catastro.by_rc(session, parcels[0].parcel_rc))
    if res is not None:
        await _show(
            message,
            res,
            state=state,
            lang=lang,
            settings=settings,
            session=session,
            user=user,
            catastro=catastro,
            method="location",
        )


@router.message(CheckFlow.location, F.text.in_(CANCEL_TEXTS))
async def location_cancel(
    message: Message, state: FSMContext, lang: str, settings: Settings
) -> None:
    await state.clear()
    await message.answer(t(lang, "common.cancelled"), reply_markup=ReplyKeyboardRemove())
    await send_menu(message, lang, settings)


@router.message(CheckFlow.location)
async def location_other(message: Message, lang: str) -> None:
    await message.answer(t(lang, "check.ask_location"))


# ---------------------------------------------------------------- unit choice


@router.callback_query(CheckFlow.pick, F.data.startswith(P + "p:"))
async def cb_page(cq: CallbackQuery, state: FSMContext, lang: str, settings: Settings) -> None:
    units = (await state.get_data()).get("units") or []
    page = int((cq.data or "k:p:0").split(":")[2])
    await cq.answer()
    if isinstance(cq.message, Message) and units:
        await cq.message.edit_reply_markup(
            reply_markup=_units_markup(units, page, settings.catastro.units_page_size, lang)
        )


@router.callback_query(CheckFlow.pick, F.data.startswith(P + "u:"))
async def cb_unit(
    cq: CallbackQuery,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    catastro: CatastroService,
) -> None:
    data = await state.get_data()
    units = data.get("units") or []
    try:
        rc = units[int((cq.data or "").split(":")[2])][0]
    except (ValueError, IndexError):
        await cq.answer(t(lang, "check.err.expired"), show_alert=True)
        return
    await cq.answer()
    if not isinstance(cq.message, Message):
        return
    res = await _safe(cq.message, lang, catastro.by_rc(session, rc))
    if res is not None:
        await _show(
            cq.message,
            res,
            state=state,
            lang=lang,
            settings=settings,
            session=session,
            user=user,
            catastro=catastro,
            method=data.get("method", "rc"),
        )


@router.message(CheckFlow.pick, F.text & ~F.text.startswith("/"))
async def pick_filter(message: Message, state: FSMContext, lang: str, settings: Settings) -> None:
    """Typing «2 1» narrows a long unit list to floor 2, door 1."""
    all_units = (await state.get_data()).get("all_units")
    if not all_units:
        await message.answer(t(lang, "check.err.use_buttons"))
        return
    idx = cd.filter_units([label for _, label in all_units], message.text or "")
    if not idx:
        await message.answer(t(lang, "check.err.filter_empty"))
        return
    units = [all_units[i] for i in idx]
    await state.update_data(units=units)
    await message.answer(
        t(lang, "check.pick_unit", count=len(units)),
        reply_markup=_units_markup(units, 0, settings.catastro.units_page_size, lang),
    )


@router.message(CheckFlow.pick)
async def pick_other(message: Message, lang: str) -> None:
    await message.answer(t(lang, "check.err.use_buttons"))


# ---------------------------------------------------------------- card actions


@router.callback_query(F.data.in_({CB_NOTA, CB_CONSULT}))
async def cb_lead(
    cq: CallbackQuery,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
) -> None:
    p = await _last_property(state, session, user)
    if p is None:
        await cq.answer(t(lang, "check.err.expired"), show_alert=True)
        return
    await cq.answer()
    lead_type = "nota_simple" if cq.data == CB_NOTA else "object_check"
    if isinstance(cq.message, Message):
        await start_lead_flow(
            cq.message, state, lang, settings, lead_type, lead_data(p, settings.admin.language)
        )


@router.callback_query(F.data == CB_CALC)
async def cb_calc(cq: CallbackQuery, state: FSMContext, lang: str, settings: Settings) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await start_calc(cq.message, state, lang, settings)


@router.callback_query(F.data.startswith(P))
async def cb_stale(cq: CallbackQuery, lang: str) -> None:
    await cq.answer(t(lang, "check.err.expired"), show_alert=True)
