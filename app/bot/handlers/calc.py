"""Calculator dialog (SPEC 5.1): step-by-step, inline buttons, «Back» on every step.

State: CalcFlow.active, data = {"answers": {...}, "history": [steps...]}.
Which steps are asked depends on previous answers (applicable()); answers to steps that
became irrelevant after «Back» are ignored when building CalcInput.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.calc_view import lead_summary, render_result
from app.bot.handlers.contact import start_lead_flow
from app.bot.handlers.start import send_menu
from app.config import Settings, get_regulation, get_tax_config
from app.core import repo
from app.core.calc import (
    AmountParseError,
    Buyer,
    CalcInput,
    Family,
    PropertyType,
    TaxConfig,
    TaxStatus,
    calculate,
    income_limit,
    parse_amount,
    parse_percent,
)
from app.core.models import Event, User
from app.core.money import format_eur
from app.i18n import t
from app.integrations.telegram_channel import SubscriptionChecker

router = Router(name="calc")

P = "c:"  # callback prefix
CB_BACK = P + "back"
CB_LEAD = P + "lead"
CB_RESTART = P + "restart"
CB_SUBCHECK = P + "subcheck"
SKIP = "-"  # «I don't know»


class CalcFlow(StatesGroup):
    active = State()


STEPS = [
    "type", "price", "ref", "buyer", "habitual", "family", "children", "age", "disability",
    "income", "large_holder", "mortgage", "mortgage_share", "status", "cadastral", "rent",
    "rent_amount",
]  # fmt: skip

INPUT_STEPS = {"price", "ref", "mortgage_share", "cadastral", "rent_amount"}
SKIPPABLE = {"ref", "cadastral"}
YES_NO = {"habitual", "age", "disability", "income", "large_holder", "mortgage", "rent"}


def applicable(step: str, a: dict[str, Any]) -> bool:
    resale = a.get("type") == PropertyType.RESALE
    habitual = resale and a.get("buyer") == Buyer.PRIVATE and a.get("habitual") is True
    family = a.get("family", Family.NONE)
    match step:
        case "habitual":
            return resale and a.get("buyer") == Buyer.PRIVATE
        case "family":
            return habitual
        case "children":
            return habitual and family != Family.NONE
        case "age":
            return habitual and family == Family.NONE
        case "disability":
            return habitual and family == Family.NONE and a.get("age") is False
        case "income":
            return habitual and (
                family != Family.NONE or a.get("age") is True or a.get("disability") is True
            )
        case "large_holder":
            return resale
        case "mortgage_share":
            return a.get("mortgage") is True
        case "cadastral":
            return a.get("status") in (TaxStatus.EU, TaxStatus.NON_EU)
        case "rent_amount":
            return a.get("rent") is True
    return True


def next_step(current: str | None, a: dict[str, Any]) -> str | None:
    start = 0 if current is None else STEPS.index(current) + 1
    for step in STEPS[start:]:
        if applicable(step, a):
            return step
    return None


def build_input(a: dict[str, Any], settings: Settings) -> CalcInput:
    def get(step: str, default: Any = None) -> Any:
        return a.get(step, default) if applicable(step, a) and step in a else default

    def dec(step: str) -> Decimal | None:
        v = get(step)
        return None if v in (None, SKIP) else Decimal(str(v))

    return CalcInput(
        property_type=PropertyType(a["type"]),
        price=Decimal(str(a["price"])),
        buyer=Buyer(a.get("buyer", Buyer.PRIVATE)),
        reference_value=dec("ref"),
        habitual=bool(get("habitual", False)),
        family=Family(get("family", Family.NONE)),
        children=int(get("children", 0)),
        age_le_limit=bool(get("age", False)),
        disability=bool(get("disability", False)),
        income_within_limit=bool(get("income", False)),
        large_holder=bool(get("large_holder", False)),
        mortgage_share=dec("mortgage_share") if get("mortgage") else None,
        tax_status=TaxStatus(a.get("status", TaxStatus.RESIDENT)),
        cadastral_value=dec("cadastral"),
        monthly_rent=dec("rent_amount") if get("rent") else None,
        municipality=settings.calc.municipality,
    )


# ---------------------------------------------------------------- questions


@dataclass
class Question:
    text: str
    markup: InlineKeyboardMarkup


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def question(
    step: str, a: dict[str, Any], lang: str, settings: Settings, cfg: TaxConfig
) -> Question:
    rows: list[list[InlineKeyboardButton]] = []
    ans = lambda v: P + f"{step}:{v}"  # noqa: E731
    params: dict[str, Any] = {}
    if step in YES_NO:
        rows.append(
            [_btn(t(lang, "calc.answer_yes"), ans(1)), _btn(t(lang, "calc.answer_no"), ans(0))]
        )
    elif step == "type":
        rows += [
            [_btn(t(lang, "calc.a.resale"), ans("resale"))],
            [_btn(t(lang, "calc.a.new"), ans("new"))],
        ]
    elif step == "buyer":
        rows.append(
            [
                _btn(t(lang, "calc.a.private"), ans("private")),
                _btn(t(lang, "calc.a.company"), ans("company")),
            ]
        )
    elif step == "family":
        rows.append(
            [
                _btn(t(lang, "calc.a.family_none"), ans("none")),
                _btn(t(lang, "calc.a.family_large"), ans("large_family")),
                _btn(t(lang, "calc.a.family_single"), ans("single_parent")),
            ]
        )
    elif step == "children":
        nums = list(range(1, 9))
        rows += [
            [_btn(str(n) if n < 8 else "8+", ans(n)) for n in nums[:4]],
            [_btn(str(n) if n < 8 else "8+", ans(n)) for n in nums[4:]],
        ]
    elif step == "status":
        rows += [[_btn(t(lang, f"calc.a.{s}"), ans(s))] for s in ("resident", "eu_eea", "non_eu")]
    elif step == "mortgage_share":
        default = settings.calc.mortgage_share_default
        rows.append(
            [
                _btn(f"{'✓ ' if p == default else ''}{p}%", ans(p))
                for p in settings.calc.mortgage_share_options
            ]
        )
        params["default"] = default
    if step in SKIPPABLE:
        rows.append([_btn(t(lang, "calc.dont_know"), ans(SKIP))])

    if step == "age":
        params["age"] = cfg.age_max
    elif step == "disability":
        params["pct"] = cfg.disability_min_pct
    elif step == "income":
        params["limit"] = format_eur(
            income_limit(cfg, Family(a.get("family", Family.NONE)), int(a.get("children", 0)))
        )
    elif step == "large_holder":
        params["dwellings"] = cfg.large_holder_dwellings_gt
        params["m2"] = f"{cfg.large_holder_m2_gt:,}".replace(",", " ")

    rows.append([_btn(t(lang, "calc.back"), CB_BACK), _btn(t(lang, "common.cancel"), kb.CB.CANCEL)])
    text = t(lang, f"calc.q.{step}", **params)
    if step == "type":
        text = t(lang, "calc.intro") + "\n\n" + text
    return Question(text, InlineKeyboardMarkup(inline_keyboard=rows))


# ---------------------------------------------------------------- flow helpers


async def _ask(
    target: Message, step: str, a: dict[str, Any], lang: str, settings: Settings, edit: bool
) -> None:
    q = question(step, a, lang, settings, get_tax_config())
    if edit:
        await target.edit_text(q.text, reply_markup=q.markup)
    else:
        await target.answer(q.text, reply_markup=q.markup)


async def start_calc(
    message: Message, state: FSMContext, lang: str, settings: Settings, edit: bool = False
) -> None:
    await state.clear()
    await state.set_state(CalcFlow.active)
    await state.update_data(answers={}, history=["type"])
    await _ask(message, "type", {}, lang, settings, edit)


def _parse_choice(step: str, raw: str) -> Any:
    if step in YES_NO:
        if raw not in ("0", "1"):
            raise ValueError(raw)
        return raw == "1"
    allowed: dict[str, set[str]] = {
        "type": {"resale", "new"},
        "buyer": {"private", "company"},
        "family": {"none", "large_family", "single_parent"},
        "status": {"resident", "eu_eea", "non_eu"},
    }
    if step in allowed:
        if raw not in allowed[step]:
            raise ValueError(raw)
        return raw
    if step == "children":
        n = int(raw)
        if not 1 <= n <= 8:
            raise ValueError(raw)
        return n
    if step == "mortgage_share":
        return str(parse_percent(raw))
    if step in SKIPPABLE and raw == SKIP:
        return SKIP
    raise ValueError(raw)


def _parse_text(step: str, text: str, settings: Settings, lang: str) -> tuple[Any, str | None]:
    """Returns (value, error_text)."""
    try:
        if step == "mortgage_share":
            return str(parse_percent(text)), None
        value = parse_amount(text)
    except AmountParseError:
        return None, t(lang, "calc.err.percent" if step == "mortgage_share" else "calc.err.amount")
    if step == "price":
        lo, hi = settings.calc.price_min_eur, settings.calc.price_max_eur
        if not lo <= value <= hi:
            return None, t(lang, "calc.err.price_range", min=format_eur(lo), max=format_eur(hi))
    return str(value), None


async def _advance(
    target: Message,
    state: FSMContext,
    step: str,
    value: Any,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    edit: bool,
    subs: SubscriptionChecker,
) -> None:
    data = await state.get_data()
    a: dict[str, Any] = dict(data.get("answers", {}))
    history: list[str] = list(data.get("history", []))
    a[step] = value
    nxt = next_step(step, a)
    if nxt is None:
        await state.update_data(answers=a, history=history)
        await _show_result(target, state, a, lang, settings, session, user, subs, edit)
        return
    history.append(nxt)
    await state.update_data(answers=a, history=history)
    await _ask(target, nxt, a, lang, settings, edit)


def _result_markup(lang: str, settings: Settings, locked: bool) -> InlineKeyboardMarkup:
    rows = []
    if locked:
        rows.append(
            [InlineKeyboardButton(text=t(lang, "calc.btn.subscribe"), url=settings.channel.url)]
        )
        rows.append([_btn(t(lang, "calc.btn.subscribed"), CB_SUBCHECK)])
    rows.append([_btn(t(lang, "calc.btn.lead"), CB_LEAD)])
    rows.append([_btn(t(lang, "calc.btn.restart"), CB_RESTART)])
    if not locked:
        rows.append([InlineKeyboardButton(text=t(lang, "menu.channel"), url=settings.channel.url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render(inp: CalcInput, lang: str, settings: Settings, locked: bool) -> str:
    cfg = get_tax_config()
    return render_result(calculate(inp, cfg), lang, get_regulation(), cfg.verified_at, locked)


async def _is_locked(user: User, settings: Settings, subs: SubscriptionChecker) -> bool:
    return settings.calc.require_subscription and not await subs.is_subscribed(user.id)


async def _show_result(
    target: Message,
    state: FSMContext,
    a: dict[str, Any],
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    subs: SubscriptionChecker,
    edit: bool,
) -> None:
    inp = build_input(a, settings)
    res = calculate(inp, get_tax_config())
    await state.set_state(None)  # dialog finished; keep data for lead / subscription check
    await state.update_data(last_input=inp.to_dict())
    await repo.log_event(
        session,
        user.id,
        "calc:done",
        {"input": inp.to_dict(), "tax": str(res.tax.total), "tax_kind": res.tax.kind.value},
        source=user.last_source or user.source,
    )
    locked = await _is_locked(user, settings, subs)
    text = await _render(inp, lang, settings, locked)
    markup = _result_markup(lang, settings, locked)
    if edit:
        await target.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await target.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def _last_input(state: FSMContext, session: AsyncSession, user: User) -> CalcInput | None:
    """From FSM (normal case) or from the last calc:done event (after a bot restart)."""
    d = (await state.get_data()).get("last_input")
    if d is None:
        ev = await session.scalar(
            select(Event)
            .where(Event.user_id == user.id, Event.event_type == "calc:done")
            .order_by(Event.id.desc())
            .limit(1)
        )
        d = (ev.payload or {}).get("input") if ev else None
    try:
        return CalcInput.from_dict(d) if d else None
    except (KeyError, ValueError):
        return None


# ---------------------------------------------------------------- handlers


@router.callback_query(F.data == kb.CB.CALC)
async def cb_start(cq: CallbackQuery, state: FSMContext, lang: str, settings: Settings) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await start_calc(cq.message, state, lang, settings)


@router.message(Command("calc"))
async def cmd_calc(message: Message, state: FSMContext, lang: str, settings: Settings) -> None:
    await start_calc(message, state, lang, settings)


@router.callback_query(F.data == CB_RESTART)
async def cb_restart(cq: CallbackQuery, state: FSMContext, lang: str, settings: Settings) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await start_calc(cq.message, state, lang, settings)


@router.callback_query(CalcFlow.active, F.data == CB_BACK)
async def cb_back(cq: CallbackQuery, state: FSMContext, lang: str, settings: Settings) -> None:
    await cq.answer()
    data = await state.get_data()
    history: list[str] = list(data.get("history", []))
    if not isinstance(cq.message, Message):
        return
    if len(history) <= 1:  # first step -> main menu
        await state.clear()
        await cq.message.edit_text(t(lang, "common.cancelled"))
        await send_menu(cq.message, lang, settings)
        return
    history.pop()
    await state.update_data(history=history)
    await _ask(cq.message, history[-1], data.get("answers", {}), lang, settings, edit=True)


@router.callback_query(CalcFlow.active, F.data.startswith(P))
async def cb_answer(
    cq: CallbackQuery,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    subscriptions: SubscriptionChecker,
) -> None:
    _, step, raw = (
        (cq.data or "").split(":", 2) if (cq.data or "").count(":") >= 2 else ("", "", "")
    )
    history = (await state.get_data()).get("history", [])
    if not history or step != history[-1]:  # stale button from an earlier message
        await cq.answer()
        return
    try:
        value = _parse_choice(step, raw)
    except (ValueError, AmountParseError):
        await cq.answer()
        return
    await cq.answer()
    if isinstance(cq.message, Message):
        await _advance(
            cq.message, state, step, value, lang, settings, session, user, True, subscriptions
        )


@router.message(CalcFlow.active, F.text & ~F.text.startswith("/"))
async def text_answer(
    message: Message,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    subscriptions: SubscriptionChecker,
) -> None:
    history = (await state.get_data()).get("history", [])
    step = history[-1] if history else None
    if step not in INPUT_STEPS:
        await message.answer(t(lang, "calc.err.use_buttons"))
        return
    value, error = _parse_text(step, message.text or "", settings, lang)
    if error:
        await message.answer(error)
        return
    await _advance(message, state, step, value, lang, settings, session, user, False, subscriptions)


@router.callback_query(F.data == CB_SUBCHECK)
async def cb_subcheck(
    cq: CallbackQuery,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
    subscriptions: SubscriptionChecker,
) -> None:
    subscriptions.invalidate(user.id)
    inp = await _last_input(state, session, user)
    if inp is None:
        await cq.answer(t(lang, "calc.err.expired"), show_alert=True)
        return
    if await _is_locked(user, settings, subscriptions):
        await cq.answer(t(lang, "calc.not_subscribed_yet"), show_alert=True)
        return
    await cq.answer()
    if isinstance(cq.message, Message):
        await cq.message.edit_text(
            await _render(inp, lang, settings, False),
            reply_markup=_result_markup(lang, settings, False),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data == CB_LEAD)
async def cb_lead(
    cq: CallbackQuery,
    state: FSMContext,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    user: User,
) -> None:
    inp = await _last_input(state, session, user)
    if inp is None:
        await cq.answer(t(lang, "calc.err.expired"), show_alert=True)
        return
    await cq.answer()
    summary = lead_summary(calculate(inp, get_tax_config()), settings.admin.language)
    if isinstance(cq.message, Message):
        await start_lead_flow(cq.message, state, lang, settings, "calc", summary)


@router.callback_query(F.data.startswith(P))
async def cb_stale(cq: CallbackQuery, lang: str) -> None:
    """Buttons of a finished/expired dialog."""
    await cq.answer(t(lang, "calc.err.expired"), show_alert=True)
