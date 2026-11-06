"""End-to-end handler tests: real dispatcher, fake Telegram API."""

from __future__ import annotations

from aiogram.methods import DeleteMessage, SendDocument, SendMessage
from aiogram.types import Contact
from sqlalchemy import func, select

from app.core.models import Event, Lead, User
from tests.conftest import ADMIN_CHAT, ADMIN_ID
from tests.helpers import cb_update, feed, msg_update, tg_user


async def test_start_ru_and_en_with_source(dp, bot, fake_session, session_factory):
    ru, en = tg_user(1, "ru"), tg_user(2, "en", "john")
    await feed(dp, bot, msg_update(ru, "/start post_123"))
    await feed(dp, bot, msg_update(en, "/start"))
    texts = [m.text for m in fake_session.sent()]
    assert any("Здравствуйте" in x for x in texts)
    assert any("Hello" in x for x in texts)
    menu = [
        m
        for m in fake_session.sent()
        if m.reply_markup is not None and hasattr(m.reply_markup, "inline_keyboard")
    ]
    assert len(menu) == 2
    assert menu[0].reply_markup.inline_keyboard[0][0].text.startswith("🧮 Рассчитать")
    assert menu[1].reply_markup.inline_keyboard[0][0].text.startswith("🧮 Purchase")
    async with session_factory() as s:
        u = await s.get(User, 1)
        assert u.source == "post_123" and u.language == "ru"
        assert (await s.get(User, 2)).language == "en"
        types = set((await s.scalars(select(Event.event_type))).all())
        assert {"cmd:start", "start"} <= types


async def test_contact_flow_with_phone_delivers_card(dp, bot, fake_session, session_factory):
    u = tg_user(3, "ru")
    await feed(dp, bot, msg_update(u, "/start site"))
    await feed(dp, bot, cb_update(u, "menu:contact"))
    assert "согласие" in fake_session.sent()[-1].text
    await feed(dp, bot, cb_update(u, "contact:consent"))
    # someone else's contact is rejected
    await feed(
        dp, bot, msg_update(u, contact=Contact(phone_number="111", first_name="X", user_id=999))
    )
    assert "свой номер" in fake_session.sent()[-1].text
    await feed(
        dp,
        bot,
        msg_update(u, contact=Contact(phone_number="34600111222", first_name="Иван", user_id=3)),
    )
    cards = [m for m in fake_session.sent() if m.chat_id == ADMIN_CHAT]
    assert len(cards) == 1
    card = cards[0].text
    assert "+34600111222" in card and "@ivan" in card and "site" in card and "Связаться" in card
    async with session_factory() as s:
        lead = (await s.scalars(select(Lead))).one()
        assert (lead.lead_type, lead.source, lead.phone) == ("contact", "site", "+34600111222")
        assert lead.admin_message_id is not None


async def test_contact_username_only_and_no_username(dp, bot, fake_session, session_factory):
    noname = tg_user(4, "en", username=None)
    await feed(dp, bot, cb_update(noname, "contact:consent"))
    await feed(dp, bot, msg_update(noname, "💬 Telegram only, no phone"))
    assert "no username" in fake_session.sent()[-1].text
    u = tg_user(5, "en", "kate")
    await feed(dp, bot, cb_update(u, "contact:consent"))
    await feed(dp, bot, msg_update(u, "💬 Telegram only, no phone"))
    async with session_factory() as s:
        leads = (await s.scalars(select(Lead))).all()
        assert [(x.user_id, x.phone) for x in leads] == [(5, None)]


async def test_stats_and_csv_admin_only(dp, bot, fake_session):
    await feed(dp, bot, msg_update(tg_user(6), "/stats"))
    assert "только администраторам" in fake_session.sent()[-1].text
    admin = tg_user(ADMIN_ID, "ru", "boss")
    await feed(dp, bot, msg_update(admin, "/leads_csv"))
    assert "Заявок пока нет" in fake_session.sent()[-1].text
    u = tg_user(8, "ru", "petr")
    await feed(dp, bot, cb_update(u, "contact:consent"))
    await feed(dp, bot, msg_update(u, "💬 Только Telegram, без телефона"))
    await feed(dp, bot, msg_update(admin, "/stats"))
    stats = fake_session.sent()[-1].text
    assert "Статистика" in stats and "Связаться: 1" in stats
    assert "Проверок объектов: 0" in stats and "Расчётов в калькуляторе: 0" in stats
    assert "Сыграно в квиз: 0" in stats
    # works from the admin group chat too
    await feed(dp, bot, msg_update(admin, "/leads_csv", chat_id=ADMIN_CHAT))
    await feed(dp, bot, msg_update(admin, "/chatid", chat_id=ADMIN_CHAT))
    assert str(ADMIN_CHAT) in fake_session.sent()[-1].text
    docs = fake_session.sent(SendDocument)
    assert len(docs) == 1 and docs[0].document.filename.endswith(".csv")


async def test_delete_me(dp, bot, fake_session, session_factory):
    u = tg_user(9, "ru")
    await feed(dp, bot, cb_update(u, "contact:consent"))
    await feed(dp, bot, msg_update(u, "💬 Только Telegram, без телефона"))
    await feed(dp, bot, msg_update(u, "/delete_me"))
    await feed(dp, bot, cb_update(u, "delete:confirm"))
    assert fake_session.sent(DeleteMessage)
    assert any("удалил свои данные" in m.text for m in fake_session.sent(SendMessage))
    async with session_factory() as s:
        assert await s.get(User, 9) is None
        assert await s.scalar(select(func.count()).select_from(Lead)) == 0
        assert await s.scalar(select(func.count()).select_from(Event)) == 0


async def test_language_switch_and_stubs(dp, bot, fake_session, session_factory):
    u = tg_user(10, "ru")
    await feed(dp, bot, msg_update(u, "/start"))
    await feed(dp, bot, cb_update(u, "lang:en"))
    assert fake_session.sent()[-1].reply_markup.inline_keyboard[0][0].text.startswith("🧮 Purchase")
    async with session_factory() as s:
        user = await s.get(User, 10)
        assert user.language == "en" and user.language_manual
    await feed(dp, bot, cb_update(u, "menu:game"))  # stub must not raise
