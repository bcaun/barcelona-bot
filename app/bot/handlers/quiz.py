"""Quiz in the bot: /game opens the Mini App; deep links from the Mini App:
?start=prize — claim the prize (free nota simple) after winning; ?start=calc / ?start=check.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.handlers.calc import start_calc
from app.bot.handlers.check import start_check
from app.bot.handlers.contact import start_lead_flow
from app.config import Settings
from app.core import repo
from app.core.models import Lead, QuizSession, User
from app.i18n import t
from app.integrations.telegram_channel import SubscriptionChecker

router = Router(name="quiz")
CB_PRIZE_CHECK = "q:prize"


def _game_markup(lang: str, settings: Settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[kb.game_button(lang, settings.quiz.webapp_url)]])


@router.message(Command("game"))
async def cmd_game(message: Message, lang: str, settings: Settings) -> None:
    await message.answer(t(lang, "quiz.invite"), reply_markup=_game_markup(lang, settings))


@router.message(CommandStart(deep_link=True, magic=F.args.in_({"calc", "check"})))
async def start_tool(message: Message, command: CommandObject, state: FSMContext, lang: str,
                     settings: Settings) -> None:  # fmt: skip
    if command.args == "calc":
        await start_calc(message, state, lang, settings)
    else:
        await start_check(message, state, lang)


async def _prize(message: Message, user: User, lang: str, settings: Settings,
                 session: AsyncSession, state: FSMContext,
                 subscriptions: SubscriptionChecker) -> None:  # fmt: skip
    best = await session.scalar(
        select(QuizSession).where(QuizSession.user_id == user.id, QuizSession.won.is_(True))
        .order_by(QuizSession.score.desc()).limit(1)
    )  # fmt: skip
    if best is None:
        await message.answer(
            t(lang, "quiz.prize_not_won"), reply_markup=_game_markup(lang, settings)
        )
        return
    claimed = await session.scalar(select(func.count()).select_from(Lead).where(
        Lead.user_id == user.id, Lead.lead_type == "game"))  # fmt: skip
    if claimed:
        await message.answer(t(lang, "quiz.prize_already"))
        return
    if settings.quiz.prize_requires_subscription and not await subscriptions.is_subscribed(user.id):
        await message.answer(t(lang, "quiz.prize_subscribe"), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=t(lang, "calc.btn.subscribe"),
                                      url=settings.channel.url)],
                [InlineKeyboardButton(text=t(lang, "calc.btn.subscribed"),
                                      callback_data=CB_PRIZE_CHECK)],
            ]))  # fmt: skip
        return
    admin_lang = settings.admin.language
    total = len((best.state or {}).get("question_ids", [])) or settings.quiz.questions_per_game
    data = {
        t(admin_lang, "quiz.lead.prize"): t(admin_lang, "quiz.lead.prize_value"),
        t(admin_lang, "quiz.lead.result"): f"{best.score}/{total}",
    }
    await message.answer(t(lang, "quiz.prize_congrats"))
    await start_lead_flow(message, state, lang, settings, "game", data)


@router.message(CommandStart(deep_link=True, magic=F.args == "prize"))
async def start_prize(message: Message, user: User, lang: str, settings: Settings,
                      session: AsyncSession, state: FSMContext,
                      subscriptions: SubscriptionChecker) -> None:  # fmt: skip
    await repo.log_event(session, user.id, "game:prize_open", None, source=user.source)
    await _prize(message, user, lang, settings, session, state, subscriptions)


@router.callback_query(F.data == CB_PRIZE_CHECK)
async def cb_prize_check(cq: CallbackQuery, user: User, lang: str, settings: Settings,
                         session: AsyncSession, state: FSMContext,
                         subscriptions: SubscriptionChecker) -> None:  # fmt: skip
    subscriptions.invalidate(user.id)
    if not await subscriptions.is_subscribed(user.id):
        await cq.answer(t(lang, "calc.not_subscribed_yet"), show_alert=True)
        return
    await cq.answer()
    if isinstance(cq.message, Message):
        await _prize(cq.message, user, lang, settings, session, state, subscriptions)
