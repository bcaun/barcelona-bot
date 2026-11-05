"""/start with deep link source, main menu, language switch, phase stubs."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.config import Settings
from app.core import repo
from app.core.models import User
from app.i18n import t

router = Router(name="start")


async def send_menu(message: Message, lang: str, settings: Settings) -> None:
    await message.answer(
        t(lang, "menu.title"), reply_markup=kb.main_menu(lang, settings.channel.url)
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    user: User,
    lang: str,
    settings: Settings,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()
    source = repo.clean_source(command.args)
    repo.apply_source(user, source)
    await repo.log_event(
        session, user.id, "start", {"source": source} if source else None, source=source
    )
    name = html.escape(message.from_user.first_name if message.from_user else "")
    await message.answer(t(lang, "start.welcome", name=name), reply_markup=ReplyKeyboardRemove())
    await send_menu(message, lang, settings)


@router.message(Command("menu"))
async def cmd_menu(message: Message, lang: str, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    await send_menu(message, lang, settings)


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str) -> None:
    await message.answer(t(lang, "help.text"))


@router.callback_query(F.data == kb.CB.MENU)
async def cb_menu(cq: CallbackQuery, lang: str, settings: Settings) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await send_menu(cq.message, lang, settings)


@router.callback_query(F.data.in_({kb.CB.CHECK, kb.CB.GAME}))
async def cb_soon(cq: CallbackQuery, lang: str) -> None:
    key = "common.soon_check" if cq.data == kb.CB.CHECK else "common.soon_game"
    await cq.answer(t(lang, key), show_alert=True)


@router.message(Command("language"))
async def cmd_language(message: Message, lang: str) -> None:
    await message.answer(t(lang, "lang.choose"), reply_markup=kb.language_menu())


@router.callback_query(F.data == kb.CB.LANGUAGE)
async def cb_language(cq: CallbackQuery, lang: str) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await cq.message.answer(t(lang, "lang.choose"), reply_markup=kb.language_menu())


@router.callback_query(F.data.startswith(kb.CB.SET_LANG))
async def cb_set_language(cq: CallbackQuery, user: User, settings: Settings) -> None:
    code = (cq.data or "")[len(kb.CB.SET_LANG) :]
    if code not in settings.i18n.supported:
        await cq.answer()
        return
    user.language = code
    user.language_manual = True
    await cq.answer(t(code, "lang.changed"))
    if isinstance(cq.message, Message):
        await cq.message.edit_text(t(code, "lang.changed"))
        await send_menu(cq.message, code, settings)


@router.callback_query(F.data == kb.CB.CANCEL)
async def cb_cancel(cq: CallbackQuery, lang: str, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    await cq.answer()
    if isinstance(cq.message, Message):
        await cq.message.edit_text(t(lang, "common.cancelled"))
        await send_menu(cq.message, lang, settings)
