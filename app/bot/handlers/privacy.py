"""/delete_me — GDPR right to erasure."""

from __future__ import annotations

import contextlib

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.config import Env, Settings
from app.core import repo
from app.core.models import User
from app.i18n import t

router = Router(name="privacy")
log = structlog.get_logger(__name__)


@router.message(Command("delete_me"))
async def cmd_delete_me(message: Message, lang: str) -> None:
    await message.answer(t(lang, "delete.confirm_text"), reply_markup=kb.delete_confirm(lang))


@router.callback_query(F.data == kb.CB.DELETE_CONFIRM)
async def cb_delete_confirm(
    cq: CallbackQuery,
    user: User,
    lang: str,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
    env: Env,
    settings: Settings,
) -> None:
    await state.clear()
    user_id = user.id
    leads = await repo.delete_user_data(session, user_id)
    await session.commit()
    log.info("user_deleted", user_id=user_id, leads=len(leads))

    if env.admin_chat_id is not None:
        for lead in leads:
            if lead.admin_message_id:
                # bots can't delete messages older than 48 h — then the notice below helps
                with contextlib.suppress(Exception):
                    await bot.delete_message(env.admin_chat_id, lead.admin_message_id)
        if leads:
            ids = ", ".join(f"#{lead.id}" for lead in leads)
            try:
                await bot.send_message(
                    env.admin_chat_id,
                    t(
                        settings.admin.language,
                        "admin.deleted_notice",
                        user_id=user_id,
                        lead_ids=ids,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                log.error("delete_notice_failed", error=type(e).__name__)

    await cq.answer()
    if isinstance(cq.message, Message):
        await cq.message.edit_text(t(lang, "delete.done"))
