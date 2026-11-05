"""Lead flow: consent → phone (request_contact) or username only → lead → admin card.

Used by «Contact us» (lead `contact`) and by other features (e.g. calculator → lead `calc`)
via start_lead_flow().
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.handlers.start import send_menu
from app.bot.notify import send_lead_card
from app.config import Env, Settings
from app.core import repo
from app.core.models import User, utcnow
from app.i18n import t

router = Router(name="contact")

USERNAME_ONLY_TEXTS = {t(x, "contact.username_only_button") for x in ("ru", "en")}
CANCEL_TEXTS = {t(x, "common.cancel") for x in ("ru", "en")}


class ContactFlow(StatesGroup):
    waiting_consent = State()
    waiting_contact = State()


async def start_lead_flow(
    message: Message,
    state: FSMContext,
    lang: str,
    settings: Settings,
    lead_type: str = "contact",
    data: dict[str, Any] | None = None,
) -> None:
    """Ask for consent; the lead is created only after explicit consent (GDPR)."""
    await state.clear()
    await state.set_state(ContactFlow.waiting_consent)
    await state.update_data(lead_type=lead_type, lead_data=data)
    await message.answer(
        t(lang, "contact.consent_text", privacy_url=settings.legal.privacy_policy_url),
        reply_markup=kb.consent(lang),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == kb.CB.CONTACT)
async def cb_contact(cq: CallbackQuery, lang: str, settings: Settings, state: FSMContext) -> None:
    await cq.answer()
    if isinstance(cq.message, Message):
        await start_lead_flow(cq.message, state, lang, settings, "contact")


@router.callback_query(F.data == kb.CB.CONSENT)
async def cb_consent(cq: CallbackQuery, lang: str, state: FSMContext) -> None:
    await cq.answer()
    await state.set_state(ContactFlow.waiting_contact)
    await state.update_data(consent_at=utcnow().isoformat())
    if isinstance(cq.message, Message):
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.message.answer(t(lang, "contact.ask_phone"), reply_markup=kb.contact_request(lang))


async def _finish(
    message: Message,
    *,
    phone: str | None,
    user: User,
    lang: str,
    settings: Settings,
    env: Env,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    await state.clear()
    consent_at = datetime.fromisoformat(data["consent_at"]) if "consent_at" in data else utcnow()
    lead_type = data.get("lead_type") or "contact"
    tg = message.from_user
    lead = await repo.create_lead(
        session,
        user,
        lead_type,
        name=tg.full_name if tg else None,
        phone=phone,
        data=data.get("lead_data"),
        consent_at=consent_at,
    )
    await repo.log_event(
        session, user.id, f"lead:{lead_type}", {"lead_id": lead.id}, source=lead.source
    )
    lead.admin_message_id = await send_lead_card(
        bot, env.admin_chat_id, lead, user, settings.admin.language
    )
    await message.answer(t(lang, "contact.thanks"), reply_markup=ReplyKeyboardRemove())
    await send_menu(message, lang, settings)


@router.message(ContactFlow.waiting_contact, F.contact)
async def got_contact(
    message: Message,
    user: User,
    lang: str,
    settings: Settings,
    env: Env,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    contact = message.contact
    if not message.from_user or not contact or contact.user_id != message.from_user.id:
        await message.answer(t(lang, "contact.not_your_contact"))
        return
    phone = contact.phone_number
    if phone and not phone.startswith("+"):
        phone = "+" + phone
    await _finish(
        message,
        phone=phone,
        user=user,
        lang=lang,
        settings=settings,
        env=env,
        session=session,
        state=state,
        bot=bot,
    )


@router.message(ContactFlow.waiting_contact, F.text.in_(USERNAME_ONLY_TEXTS))
async def username_only(
    message: Message,
    user: User,
    lang: str,
    settings: Settings,
    env: Env,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    if not message.from_user or not message.from_user.username:
        await message.answer(
            t(lang, "contact.need_username"), reply_markup=kb.contact_request(lang)
        )
        return
    await _finish(
        message,
        phone=None,
        user=user,
        lang=lang,
        settings=settings,
        env=env,
        session=session,
        state=state,
        bot=bot,
    )


@router.message(ContactFlow.waiting_contact, F.text.in_(CANCEL_TEXTS))
async def cancel(message: Message, lang: str, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    await message.answer(t(lang, "common.cancelled"), reply_markup=ReplyKeyboardRemove())
    await send_menu(message, lang, settings)


@router.message(ContactFlow.waiting_contact)
async def other(message: Message, lang: str) -> None:
    await message.answer(t(lang, "contact.use_buttons"), reply_markup=kb.contact_request(lang))


@router.message(ContactFlow.waiting_consent)
async def other_consent(message: Message, lang: str) -> None:
    await message.answer(t(lang, "calc.err.use_buttons"))
