"""Admin commands: /stats, /leads_csv (only for ADMIN_IDS)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, Filter
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Env, Settings
from app.core import repo
from app.core.models import Lead
from app.i18n import t

router = Router(name="admin")


class IsAdmin(Filter):
    async def __call__(self, message: Message, env: Env) -> bool:
        return bool(message.from_user and message.from_user.id in env.admin_id_set)


def _lines(counts: dict[str, int], lang: str, label_prefix: str | None = None) -> str:
    if not counts:
        return t(lang, "admin.no_value")
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    out = []
    for k, n in items:
        label = k
        if label_prefix:
            tr = t(lang, f"{label_prefix}.{k}")
            label = tr if tr != f"{label_prefix}.{k}" else k
        out.append(f"• {label}: {n}")
    return "\n".join(out)


@router.message(Command("stats"), IsAdmin())
async def cmd_stats(message: Message, session: AsyncSession, settings: Settings) -> None:
    lang = settings.admin.language
    s = await repo.get_stats(session, settings.timezone)
    await message.answer(
        t(
            lang,
            "admin.stats",
            users_today=s.users_today,
            users_week=s.users_week,
            users_total=s.users_total,
            leads_today=s.leads_today,
            leads_week=s.leads_week,
            leads_total=s.leads_total,
            by_type=_lines(s.leads_by_type, lang, "admin.lead_types"),
            by_source=_lines(s.leads_by_source, lang),
            checks_week=s.checks_week,
            calcs_week=s.calcs_week,
            check_flags=_lines(s.check_flags_week, lang, "check.flag"),
        )
    )


@router.message(Command("leads_csv"), IsAdmin())
async def cmd_leads_csv(message: Message, session: AsyncSession, settings: Settings) -> None:
    lang = settings.admin.language
    count = int(await session.scalar(select(func.count()).select_from(Lead)) or 0)
    if count == 0:
        await message.answer(t(lang, "admin.csv_empty"))
        return
    data = await repo.leads_csv(session, settings.timezone)
    stamp = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d_%H%M")
    await message.answer_document(
        BufferedInputFile(data, filename=f"leads_{stamp}.csv"),
        caption=t(lang, "admin.csv_caption", count=count),
    )


@router.message(Command("chatid"), IsAdmin())
async def cmd_chatid(message: Message) -> None:
    """Helps the owner find ADMIN_CHAT_ID: send /chatid in the admin group."""
    await message.answer(f"Chat ID: <code>{message.chat.id}</code>")


@router.message(Command("stats", "leads_csv", "chatid"))
async def not_admin(message: Message, lang: str) -> None:
    await message.answer(t(lang, "common.admin_only"))
