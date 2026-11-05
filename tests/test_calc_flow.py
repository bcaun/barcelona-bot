"""Calculator dialog end-to-end: all branches, «Back», result, lead `calc`."""

from __future__ import annotations

import pytest
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import ChatMemberMember
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.core.models import Event, Lead
from tests.conftest import ADMIN_CHAT
from tests.helpers import cb_update, feed, msg_update, tg_user


def texts(fake):
    return [c.text for c in fake.calls if isinstance(c, SendMessage | EditMessageText)]


def last(fake):
    return texts(fake)[-1]


def buttons(fake):
    c = [c for c in fake.calls if isinstance(c, SendMessage | EditMessageText)][-1]
    return [b.callback_data or b.url for row in c.reply_markup.inline_keyboard for b in row]


class Dialog:
    def __init__(self, dp, bot, fake, user):
        self.dp, self.bot, self.fake, self.user = dp, bot, fake, user

    async def cb(self, data):
        await feed(self.dp, self.bot, cb_update(self.user, data))
        return last(self.fake)

    async def say(self, text):
        await feed(self.dp, self.bot, msg_update(self.user, text))
        return last(self.fake)


@pytest.fixture
def dlg(dp, bot, fake_session):
    return Dialog(dp, bot, fake_session, tg_user(50, "ru"))


async def test_resale_general_resident_full_path(dlg):
    assert "Какой объект" in await dlg.cb("menu:calc")
    assert "Цена покупки" in await dlg.cb("c:type:resale")
    assert "Не понял сумму" in await dlg.say("abc")
    assert "valor de referencia" in await dlg.say("800k")
    assert "Кто покупает" in await dlg.cb("c:ref:-")
    assert "vivienda habitual" in await dlg.cb("c:buyer:private")
    assert "более чем 10" in await dlg.cb("c:habitual:0")
    assert "ипотекой" in await dlg.cb("c:large_holder:0")
    assert "Какая доля" in await dlg.cb("c:mortgage:1")
    assert "налоговый статус" in await dlg.say("70%")
    assert "аренду" in await dlg.cb("c:status:resident")
    res = await dlg.cb("c:rent:0")
    assert "82 000 €" in res
    assert "кредит 70%" in res and "560 000 €" in res
    assert "Valor de referencia не указан" in res
    assert "не является налоговой консультацией" in res
    assert "c:lead" in buttons(dlg.fake) and "c:restart" in buttons(dlg.fake)


async def test_reduced_young_and_back_on_every_step(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    await dlg.say("300 000")
    await dlg.say("280000")  # VR lower than price
    await dlg.cb("c:buyer:private")
    await dlg.cb("c:habitual:1")
    assert "многодетная" in last(dlg.fake)
    assert "35 лет" in await dlg.cb("c:family:none")
    assert "36 000" in await dlg.cb("c:age:1")
    # Back from income -> age -> family -> habitual
    assert "35 лет" in await dlg.cb("c:back")
    assert "многодетная" in await dlg.cb("c:back")
    assert "vivienda habitual" in await dlg.cb("c:back")
    await dlg.cb("c:habitual:1")
    await dlg.cb("c:family:none")
    await dlg.cb("c:age:1")
    await dlg.cb("c:income:1")
    await dlg.cb("c:large_holder:0")
    await dlg.cb("c:mortgage:0")
    await dlg.cb("c:status:resident")
    res = await dlg.cb("c:rent:0")
    assert "льготная ставка 5%" in res and "15 000 €" in res


async def test_family_children_raise_income_limit(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    await dlg.say("300000")
    await dlg.cb("c:ref:-")
    await dlg.cb("c:buyer:private")
    await dlg.cb("c:habitual:1")
    assert "Сколько детей" in await dlg.cb("c:family:large_family")
    assert "64 000" in await dlg.cb("c:children:5")  # 36k + 2 × 14k


async def test_disability_branch(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    await dlg.say("300000")
    await dlg.cb("c:ref:-")
    await dlg.cb("c:buyer:private")
    await dlg.cb("c:habitual:1")
    await dlg.cb("c:family:none")
    assert "инвалидность 65%" in await dlg.cb("c:age:0")
    assert "база IRPF" in await dlg.cb("c:disability:1")
    await dlg.cb("c:income:0")  # fails income test
    await dlg.cb("c:large_holder:0")
    await dlg.cb("c:mortgage:0")
    await dlg.cb("c:status:resident")
    assert "общая шкала" in await dlg.cb("c:rent:0")


async def test_no_conditions_skips_income(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    await dlg.say("300000")
    await dlg.cb("c:ref:-")
    await dlg.cb("c:buyer:private")
    await dlg.cb("c:habitual:1")
    await dlg.cb("c:family:none")
    await dlg.cb("c:age:0")
    assert "более чем 10" in await dlg.cb("c:disability:0")


async def test_company_large_holder_nonresident_with_rent(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    await dlg.say("400000")
    await dlg.cb("c:ref:-")
    assert "более чем 10" in await dlg.cb("c:buyer:company")  # no habitual question
    await dlg.cb("c:large_holder:1")
    await dlg.cb("c:mortgage:0")
    assert "valor catastral" in await dlg.cb("c:status:non_eu")
    assert "аренду" in await dlg.say("150 000")
    assert "в месяц" in await dlg.cb("c:rent:1")
    res = await dlg.say("1500")
    assert "80 000 €" in res and "крупных собственников" in res
    assert "Валовая доходность" in res and "24% с валового" in res
    assert "туристические лицензии" in res and "индексом" in res
    assert "IRNR" not in res  # renting -> no imputed income tax


async def test_new_build_eu_nonresident_irnr(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:new")
    await dlg.say("400000")
    await dlg.cb("c:ref:-")
    assert "ипотекой" in await dlg.cb("c:buyer:private")  # no habitual / large holder
    await dlg.cb("c:mortgage:0")
    await dlg.cb("c:status:eu_eea")
    await dlg.say("150000")
    res = await dlg.cb("c:rent:0")
    assert "IVA: 400 000 € × 10% = 40 000 €" in res
    assert "AJD: 400 000 € × 1,5% = 6 000 €" in res
    assert "570 € в год" in res


async def test_back_from_first_step_returns_menu_and_stale_buttons(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:back")
    assert "Главное меню" in last(dlg.fake)
    assert "устарел" not in last(dlg.fake)
    n = len(dlg.fake.calls)
    await dlg.cb("c:type:resale")  # dialog closed -> alert, no new question
    assert not [c for c in dlg.fake.calls[n:] if isinstance(c, SendMessage | EditMessageText)]


async def test_price_range_and_percent_errors(dlg):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    assert "Цена должна быть" in await dlg.say("500")
    await dlg.say("500000")
    await dlg.cb("c:ref:-")
    await dlg.cb("c:buyer:company")
    await dlg.cb("c:large_holder:0")
    await dlg.cb("c:mortgage:1")
    assert "от 1 до 100" in await dlg.say("150")
    assert "кнопкой" in (await dlg.cb("c:mortgage_share:60") and await dlg.say("hello"))


async def _finish_simple(dlg, price="500000"):
    await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    await dlg.say(price)
    await dlg.cb("c:ref:-")
    await dlg.cb("c:buyer:company")
    await dlg.cb("c:large_holder:0")
    await dlg.cb("c:mortgage:0")
    await dlg.cb("c:status:resident")
    return await dlg.cb("c:rent:0")


async def test_lead_calc_reaches_admin_chat(dlg, session_factory):
    await _finish_simple(dlg)
    assert "согласие" in await dlg.cb("c:lead")
    await dlg.cb("contact:consent")
    await dlg.say("💬 Только Telegram, без телефона")
    cards = [c for c in dlg.fake.sent() if c.chat_id == ADMIN_CHAT]
    assert len(cards) == 1
    card = cards[0].text
    assert "Калькулятор" in card and "500 000 €" in card and "ITP, общая шкала" in card
    async with session_factory() as s:
        lead = (await s.scalars(select(Lead))).one()
        assert lead.lead_type == "calc" and lead.data["Налог на покупку"] == "50 000 €"
        assert (await s.scalars(select(Event).where(Event.event_type == "calc:done"))).first()


async def test_lead_after_restart_uses_db_event(
    dp, bot, fake_session, settings, env, session_factory
):
    from app.bot.factory import build_dispatcher

    u = tg_user(51, "ru")
    d1 = Dialog(dp, bot, fake_session, u)
    await _finish_simple(d1, "700000")
    # simulate bot restart: fresh dispatcher = empty FSM storage
    for r in dp.sub_routers:
        r._parent_router = None
    dp2 = build_dispatcher(bot, settings, env, session_factory)
    d2 = Dialog(dp2, bot, fake_session, u)
    assert "согласие" in await d2.cb("c:lead")


async def test_subscription_gate(dp, bot, fake_session, settings, session_factory):
    settings.calc.require_subscription = True
    try:
        dlg = Dialog(dp, bot, fake_session, tg_user(52, "ru"))
        res = await _finish_simple(dlg)
        assert "доступны подписчикам" in res and "Ежегодные расходы владения" not in res
        assert "c:subcheck" in buttons(fake_session)
        n = len(texts(fake_session))
        await dlg.cb("c:subcheck")  # still not subscribed -> alert only
        assert len(texts(fake_session)) == n

        fake_session.chat_member_factory = lambda m: ChatMemberMember(
            user=TgUser(id=m.user_id, is_bot=False, first_name="x")
        )
        res = await dlg.cb("c:subcheck")
        assert "Ежегодные расходы владения" in res
    finally:
        settings.calc.require_subscription = False


async def test_english_dialog(dp, bot, fake_session):
    dlg = Dialog(dp, bot, fake_session, tg_user(53, "en", "joe"))
    assert "What kind of property" in await dlg.cb("menu:calc")
    await dlg.cb("c:type:resale")
    await dlg.say("1m")
    await dlg.cb("c:ref:-")
    await dlg.cb("c:buyer:company")
    await dlg.cb("c:large_holder:0")
    await dlg.cb("c:mortgage:0")
    await dlg.cb("c:status:resident")
    res = await dlg.cb("c:rent:0")
    assert "105 000 €" in res and "not tax advice" in res
