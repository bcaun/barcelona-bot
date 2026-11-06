"""Outer middlewares: throttling, DB session, user registration, event logging."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core import repo
from app.i18n import pick_language, t

log = structlog.get_logger(__name__)
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ThrottlingMiddleware(BaseMiddleware):
    """Drops updates when a user sends more than N per second (sliding 1-second window)."""

    def __init__(self, max_per_second: float, clock: Callable[[], float] = time.monotonic):
        self.limit = max(1, int(max_per_second))
        self.clock = clock
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = self.clock()
        hits = self._hits[user_id]
        while hits and now - hits[0] >= 1.0:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]):
        user = data.get("event_from_user")
        if user is None or self.allow(user.id):
            return await handler(event, data)
        log.info("throttled", user_id=user.id)
        if isinstance(event, Update) and event.callback_query:
            lang = pick_language(user.language_code, ["ru", "en"], ["ru"], "en")
            await event.callback_query.answer(t(lang, "common.too_fast"))
        return None


class DbSessionMiddleware(BaseMiddleware):
    """One DB session + transaction per update; committed if the handler succeeds."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]):
        async with self.session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result


def describe_event(update: Update) -> tuple[str, dict[str, Any] | None] | None:
    """Event type without message text (we never store conversation content)."""
    if update.message:
        m: Message = update.message
        if m.text and m.text.startswith("/"):
            return f"cmd:{m.text.split()[0][1:].split('@')[0][:40]}", None
        if m.contact:
            return "message:contact", None
        if m.location:
            return "message:location", None
        if m.document:
            return "message:document", None
        if m.successful_payment:
            return "message:payment", None
        return "message", None
    if update.callback_query:
        cq: CallbackQuery = update.callback_query
        return f"cb:{(cq.data or '')[:60]}", None
    return None


class UserMiddleware(BaseMiddleware):
    """Registers the user, resolves language, logs an event for every update."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]):
        tg_user = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")
        if tg_user is None or session is None or tg_user.is_bot:
            return await handler(event, data)
        chat = data.get("event_chat")
        if chat is not None and chat.type != "private":
            # Group chats (e.g. admin chat): no registration, but pass language for replies
            data["lang"] = self.settings.admin.language
            return await handler(event, data)

        i18n = self.settings.i18n
        lang = pick_language(tg_user.language_code, i18n.supported, i18n.ru_like, i18n.default)
        user, created = await repo.get_or_create_user(session, tg_user.id, tg_user.username, lang)
        data["user"] = user
        data["lang"] = user.language
        data["user_created"] = created
        if isinstance(event, Update):
            described = describe_event(event)
            if described:
                await repo.log_event(
                    session,
                    user.id,
                    described[0],
                    described[1],
                    source=user.last_source or user.source,
                )
        return await handler(event, data)
