"""Channel subscription check via getChatMember (the bot must be an admin of the channel)."""

from __future__ import annotations

import time
from collections.abc import Callable

import structlog
from aiogram import Bot
from aiogram.enums import ChatMemberStatus

log = structlog.get_logger(__name__)

_MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}


class SubscriptionChecker:
    def __init__(
        self,
        bot: Bot,
        channel_id: str | int,
        ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._channel_id = channel_id
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[int, tuple[bool, float]] = {}

    def invalidate(self, user_id: int) -> None:
        """Drop cached value (e.g. after the user pressed «I subscribed»)."""
        self._cache.pop(user_id, None)

    async def is_subscribed(self, user_id: int) -> bool:
        now = self._clock()
        cached = self._cache.get(user_id)
        if cached and now - cached[1] < self._ttl:
            return cached[0]
        try:
            member = await self._bot.get_chat_member(self._channel_id, user_id)
        except Exception as e:  # noqa: BLE001 — API errors must not crash the bot
            log.warning("subscription_check_failed", user_id=user_id, error=type(e).__name__)
            return False  # not cached: retry on next call
        status = member.status
        subscribed = status in _MEMBER_STATUSES or (
            status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False)
        )
        self._cache[user_id] = (subscribed, now)
        return subscribed
