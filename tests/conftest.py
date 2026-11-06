from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import GetChatMember, SendDocument, SendInvoice, SendMessage, TelegramMethod
from aiogram.types import Chat, ChatMemberLeft, Message
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.factory import build_dispatcher
from app.bot.middlewares import ThrottlingMiddleware
from app.config import Env, get_settings
from app.core.db import Base, make_engine, make_session_factory

os.environ.setdefault("BOT_TOKEN", "42:TEST")

ADMIN_ID = 1000
ADMIN_CHAT = -100500


class FakeSession(BaseSession):
    """Records Telegram API calls instead of sending them."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self._msg_id = 0
        self.chat_member_factory = None

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        self.calls.append(method)
        if isinstance(method, SendMessage | SendDocument | SendInvoice):
            self._msg_id += 1
            return Message(
                message_id=self._msg_id,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=getattr(method, "text", None),
            )
        if isinstance(method, GetChatMember):
            if self.chat_member_factory:
                return self.chat_member_factory(method)
            return ChatMemberLeft(user=TgUser(id=method.user_id, is_bot=False, first_name="x"))
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        pass

    def sent(self, cls=SendMessage) -> list[Any]:
        return [c for c in self.calls if isinstance(c, cls)]


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def env() -> Env:
    return Env(
        bot_token="42:TEST",
        admin_ids=str(ADMIN_ID),
        admin_chat_id=ADMIN_CHAT,
        channel_id="@test_channel",
        database_url="sqlite+aiosqlite:///:memory:",
    )


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def bot(fake_session: FakeSession) -> Bot:
    return Bot(
        "42:TEST", session=fake_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def dp(bot, settings, env, session_factory):
    d = build_dispatcher(bot, settings, env, session_factory)
    for m in d.update.outer_middleware:  # disable throttling for scripted tests
        if isinstance(m, ThrottlingMiddleware):
            m.limit = 10_000
    yield d
    for r in d.sub_routers:  # module-level routers attach to one dispatcher at a time
        r._parent_router = None


@pytest.fixture
def make_dlg(dp, bot, fake_session):
    from app.config import CatastroSettings
    from app.integrations.catastro import CatastroClient, CatastroService
    from tests.helpers import tg_user
    from tests.helpers_catastro import Dialog, catastro_router

    def make(down=False, uid=70, lang="ru"):
        transport, seen = catastro_router(down)
        cfg = CatastroSettings(
            retries=0, retry_pause_seconds=0, card_url_template="https://sede/?RefC={rc}"
        )
        dp["catastro"] = CatastroService(CatastroClient(cfg, transport), cfg)
        return Dialog(dp, bot, fake_session, tg_user(uid, lang)), seen

    return make
