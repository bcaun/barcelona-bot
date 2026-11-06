"""Entrypoint: python -m app.bot"""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo

from app.bot.factory import build_dispatcher, set_commands
from app.config import get_env, get_settings
from app.core.db import make_engine, make_session_factory
from app.logging_setup import setup_logging


async def main() -> None:
    env = get_env()
    setup_logging(env.log_level)
    log = structlog.get_logger("app.bot")
    settings = get_settings()

    engine = make_engine(env.database_url)
    bot = Bot(
        env.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = build_dispatcher(bot, settings, env, make_session_factory(engine))

    me = await bot.get_me()
    log.info(
        "bot_starting",
        username=me.username,
        admins=len(env.admin_id_set),
        admin_chat_configured=env.admin_chat_id is not None,
    )
    try:
        await set_commands(bot, settings)
        if settings.quiz.webapp_url:  # «🎮 Квиз» button next to the message input
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                text="🎮 Квиз", web_app=WebAppInfo(url=settings.quiz.webapp_url)))  # fmt: skip
    except Exception as e:  # noqa: BLE001
        log.warning("set_commands_failed", error=type(e).__name__)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
