"""Bot side of the quiz: game button, deep links, prize claim."""

from __future__ import annotations

import uuid

from aiogram.types import ChatMemberMember
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.bot.factory import build_dispatcher
from app.core.models import Lead, QuizSession, User, utcnow
from tests.conftest import ADMIN_CHAT
from tests.helpers import cb_update, feed, msg_update, tg_user


def last(fake):
    return fake.sent()[-1]


async def _won(session_factory, uid, score=23, won=True):
    async with session_factory() as s:
        if await s.get(User, uid) is None:
            s.add(User(id=uid, language="ru", created_at=utcnow()))
            await s.flush()
        s.add(QuizSession(id=str(uuid.uuid4()), user_id=uid, local_date="2026-11-06",
                          state={"question_ids": ["x"] * 25}, score=score, won=won,
                          started_at=utcnow(), finished_at=utcnow()))  # fmt: skip
        await s.commit()


def subscribe(fake):
    fake.chat_member_factory = lambda m: ChatMemberMember(
        user=TgUser(id=m.user_id, is_bot=False, first_name="x")
    )


async def test_game_button_opens_webapp(bot, fake_session, settings, env, session_factory):
    env = env.model_copy(update={"webapp_domain": "barcelona.daagamma.com"})
    dp = build_dispatcher(bot, settings, env, session_factory)
    try:
        await feed(dp, bot, msg_update(tg_user(1), "/start"))
        rows = last(fake_session).reply_markup.inline_keyboard
        game = rows[2][0]
        assert game.web_app.url == "https://barcelona.daagamma.com/" and "Квиз" in game.text
        await feed(dp, bot, msg_update(tg_user(1), "/game"))
        assert last(fake_session).reply_markup.inline_keyboard[0][0].web_app is not None
    finally:
        for r in dp.sub_routers:
            r._parent_router = None


async def test_prize_requires_win(dp, bot, fake_session):
    await feed(dp, bot, msg_update(tg_user(10), "/start prize"))
    assert "прошёл квиз до конца" in last(fake_session).text


async def test_prize_requires_subscription_then_claims(dp, bot, fake_session, session_factory):
    await _won(session_factory, 11)
    u = tg_user(11)
    await feed(dp, bot, msg_update(u, "/start prize"))
    assert "подписчикам нашего канала" in last(fake_session).text
    await feed(dp, bot, cb_update(u, "q:prize"))  # still not subscribed -> alert only
    subscribe(fake_session)
    await feed(dp, bot, cb_update(u, "q:prize"))
    texts = [m.text for m in fake_session.sent()]
    assert any("Поздравляем" in x for x in texts) and "согласие" in texts[-1]
    await feed(dp, bot, cb_update(u, "contact:consent"))
    await feed(dp, bot, msg_update(u, "💬 Только Telegram, без телефона"))
    card = [m.text for m in fake_session.sent() if m.chat_id == ADMIN_CHAT][-1]
    assert "Квиз (приз)" in card and "Бесплатная nota simple (квиз)" in card and "23/25" in card
    async with session_factory() as s:
        assert (await s.scalars(select(Lead))).one().lead_type == "game"
    await feed(dp, bot, msg_update(u, "/start prize"))
    assert "уже получили приз" in last(fake_session).text


async def test_lost_game_gives_no_prize(dp, bot, fake_session, session_factory):
    await _won(session_factory, 12, score=5, won=False)
    subscribe(fake_session)
    await feed(dp, bot, msg_update(tg_user(12), "/start prize"))
    assert "прошёл квиз до конца" in last(fake_session).text


async def test_tool_deep_links(dp, bot, fake_session):
    await feed(dp, bot, msg_update(tg_user(13), "/start calc"))
    assert "Какой объект" in last(fake_session).text
    await feed(dp, bot, msg_update(tg_user(13), "/start check"))
    assert "Как будем искать" in last(fake_session).text


async def test_delete_me_removes_quiz_sessions(dp, bot, session_factory):
    await _won(session_factory, 14)
    await feed(dp, bot, msg_update(tg_user(14), "/delete_me"))
    await feed(dp, bot, cb_update(tg_user(14), "delete:confirm"))
    async with session_factory() as s:
        assert (await s.scalars(select(QuizSession))).first() is None
