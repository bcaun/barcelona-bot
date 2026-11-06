"""Dispatcher assembly (shared by the entrypoint and tests)."""

from __future__ import annotations

import contextlib

import structlog
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, ErrorEvent, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import admin, calc, check, contact, payments, privacy, start
from app.bot.middlewares import DbSessionMiddleware, ThrottlingMiddleware, UserMiddleware
from app.config import Env, Settings
from app.i18n import t
from app.integrations.catastro import CatastroClient, CatastroService
from app.integrations.telegram_channel import SubscriptionChecker

log = structlog.get_logger(__name__)


def build_dispatcher(
    bot: Bot, settings: Settings, env: Env, session_factory: async_sessionmaker[AsyncSession]
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["settings"] = settings
    dp["env"] = env
    dp["catastro"] = CatastroService(CatastroClient(settings.catastro), settings.catastro)
    dp["subscriptions"] = SubscriptionChecker(
        bot, env.channel_id, settings.channel.subscription_cache_seconds
    )

    dp.update.outer_middleware(ThrottlingMiddleware(settings.throttle.max_events_per_second))
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(UserMiddleware(settings))

    dp.include_routers(
        payments.router,
        admin.router,
        privacy.router,
        contact.router,
        calc.router,
        check.router,
        start.router,
    )

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("handler_error", exc_info=event.exception)
        upd = event.update
        target: Message | None = upd.message or (
            upd.callback_query.message
            if upd.callback_query and isinstance(upd.callback_query.message, Message)
            else None
        )
        user = (
            upd.message.from_user
            if upd.message
            else (upd.callback_query.from_user if upd.callback_query else None)
        )
        if target is not None:
            lang = "ru" if user and (user.language_code or "").startswith("ru") else "en"
            with contextlib.suppress(Exception):
                await target.answer(t(lang, "common.error"))
        return True

    return dp


async def set_commands(bot: Bot, settings: Settings) -> None:
    for lang in settings.i18n.supported:
        cmds = [
            BotCommand(command=c, description=t(lang, f"commands.{c}"))
            for c in ("start", "calc", "check", "language", "delete_me")
        ]
        code = None if lang == settings.i18n.default else lang
        await bot.set_my_commands(cmds, scope=BotCommandScopeDefault(), language_code=code)
