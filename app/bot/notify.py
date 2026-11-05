"""Lead cards for the admin chat."""

from __future__ import annotations

import html
import json
from typing import Any

import structlog
from aiogram import Bot

from app.core.models import Lead, User
from app.i18n import t

log = structlog.get_logger(__name__)


def format_lead_card(lead: Lead, user: User, lang: str) -> str:
    none = t(lang, "admin.no_value")

    def esc(v: Any) -> str:
        return html.escape(str(v)) if v not in (None, "") else none

    type_key = f"admin.lead_types.{lead.lead_type}"
    type_label = t(lang, type_key)
    if type_label == type_key:
        type_label = lead.lead_type
    text = t(
        lang,
        "admin.lead_card",
        id=lead.id,
        lead_type=esc(type_label),
        name=esc(lead.name),
        username=esc(f"@{lead.username}" if lead.username else None),
        phone=esc(lead.phone),
        language=esc(user.language),
        source=esc(lead.source),
        user_id=user.id,
    )
    if lead.data:
        lines = [f"• {html.escape(str(k))}: {html.escape(_fmt(v))}" for k, v in lead.data.items()]
        text += "\n\n" + t(lang, "admin.lead_data_header") + "\n" + "\n".join(lines)
    return text


def _fmt(v: Any) -> str:
    if isinstance(v, dict | list):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


async def send_lead_card(
    bot: Bot, admin_chat_id: int | None, lead: Lead, user: User, lang: str
) -> int | None:
    """Send the card, return its message id. Errors are logged, never raised."""
    if admin_chat_id is None:
        log.error("admin_chat_not_configured", lead_id=lead.id)
        return None
    try:
        msg = await bot.send_message(admin_chat_id, format_lead_card(lead, user, lang))
    except Exception as e:  # noqa: BLE001
        log.error("lead_notify_failed", lead_id=lead.id, error=type(e).__name__)
        return None
    return msg.message_id
