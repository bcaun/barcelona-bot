"""Data access functions (no Telegram dependencies)."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Event, Lead, User, utcnow

_SOURCE_RE = re.compile(r"[^A-Za-z0-9_\-]")


def clean_source(raw: str | None) -> str | None:
    """Deep link payload -> safe source tag (Telegram allows A-Z a-z 0-9 _ -, max 64)."""
    if not raw:
        return None
    s = _SOURCE_RE.sub("", raw.strip())[:64]
    return s or None


async def get_or_create_user(
    session: AsyncSession, user_id: int, username: str | None, language: str
) -> tuple[User, bool]:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, username=username, language=language, created_at=utcnow())
        session.add(user)
        await session.flush()
        return user, True
    if user.username != username:
        user.username = username
    return user, False


def apply_source(user: User, source: str | None) -> None:
    """First touch is kept forever; last touch is updated on every deep link."""
    if not source:
        return
    if user.source is None:
        user.source = source
    user.last_source = source


async def log_event(
    session: AsyncSession,
    user_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: str | None = None,
) -> None:
    session.add(Event(user_id=user_id, event_type=event_type[:64], payload=payload, source=source))


async def create_lead(
    session: AsyncSession,
    user: User,
    lead_type: str,
    *,
    name: str | None,
    phone: str | None,
    data: dict[str, Any] | None = None,
    consent_at: datetime | None = None,
) -> Lead:
    lead = Lead(
        user_id=user.id,
        lead_type=lead_type,
        source=user.last_source or user.source,
        name=name,
        username=user.username,
        phone=phone,
        data=data,
        consent_at=consent_at or utcnow(),
        created_at=utcnow(),
    )
    session.add(lead)
    await session.flush()
    return lead


async def delete_user_data(session: AsyncSession, user_id: int) -> list[Lead]:
    """Delete user, their events and leads. Returns deleted leads (to clean admin chat)."""
    leads = list((await session.scalars(select(Lead).where(Lead.user_id == user_id))).all())
    await session.execute(delete(Lead).where(Lead.user_id == user_id))
    await session.execute(delete(Event).where(Event.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    return leads


@dataclass
class Stats:
    users_today: int
    users_week: int
    users_total: int
    leads_today: int
    leads_week: int
    leads_total: int
    leads_by_type: dict[str, int] = field(default_factory=dict)
    leads_by_source: dict[str, int] = field(default_factory=dict)


def period_starts(now: datetime, tz: str) -> tuple[datetime, datetime]:
    """Start of local day and of the 7-day window, both returned in UTC."""
    local = now.astimezone(ZoneInfo(tz))
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=6)
    return day_start.astimezone(UTC), week_start.astimezone(UTC)


async def get_stats(session: AsyncSession, tz: str, now: datetime | None = None) -> Stats:
    day, week = period_starts(now or utcnow(), tz)

    async def count(model, since: datetime | None = None) -> int:  # noqa: ANN001
        q = select(func.count()).select_from(model)
        if since is not None:
            q = q.where(model.created_at >= since)
        return int(await session.scalar(q) or 0)

    by_type = dict(
        (await session.execute(select(Lead.lead_type, func.count()).group_by(Lead.lead_type))).all()
    )
    by_source_rows = (
        await session.execute(select(Lead.source, func.count()).group_by(Lead.source))
    ).all()
    by_source = {(s or "—"): n for s, n in by_source_rows}
    return Stats(
        users_today=await count(User, day),
        users_week=await count(User, week),
        users_total=await count(User),
        leads_today=await count(Lead, day),
        leads_week=await count(Lead, week),
        leads_total=await count(Lead),
        leads_by_type=by_type,
        leads_by_source=by_source,
    )


CSV_COLUMNS = [
    "id",
    "created_at",
    "lead_type",
    "source",
    "name",
    "username",
    "phone",
    "telegram_id",
    "language",
    "data",
]


async def leads_csv(session: AsyncSession, tz: str) -> bytes:
    """CSV for Excel: UTF-8 with BOM, ';' separator, local time."""
    rows = (
        await session.execute(
            select(Lead, User.language).join(User, User.id == Lead.user_id).order_by(Lead.id)
        )
    ).all()
    zone = ZoneInfo(tz)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(CSV_COLUMNS)
    for lead, language in rows:
        created = lead.created_at
        if created.tzinfo is None:  # SQLite returns naive UTC
            created = created.replace(tzinfo=UTC)
        w.writerow(
            [
                lead.id,
                created.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
                lead.lead_type,
                lead.source or "",
                lead.name or "",
                f"@{lead.username}" if lead.username else "",
                lead.phone or "",
                lead.user_id,
                language,
                json.dumps(lead.data, ensure_ascii=False) if lead.data else "",
            ]
        )
    return ("﻿" + buf.getvalue()).encode("utf-8")
