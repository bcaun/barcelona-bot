"""Paid nota simple through Telegram Payments (Stripe) — with a fake Telegram API."""

from __future__ import annotations

import itertools

import pytest
from aiogram.methods import AnswerPreCheckoutQuery, SendInvoice
from aiogram.types import OrderInfo, PreCheckoutQuery, SuccessfulPayment, Update
from pydantic import SecretStr
from sqlalchemy import select

from app.core.models import Event, Lead
from tests.conftest import ADMIN_CHAT
from tests.helpers import msg_update, tg_user
from tests.helpers_catastro import UNIT

_ids = itertools.count(10_000)


@pytest.fixture
def paid_env(dp, env):
    dp["env"] = env.model_copy(update={"payment_provider_token": SecretStr("TEST:provider-token")})
    return dp["env"]


def pre_checkout(user, amount=2900, payload=f"nota:{UNIT}", currency="EUR"):
    return Update(update_id=next(_ids), pre_checkout_query=PreCheckoutQuery(
        id=str(next(_ids)), from_user=user, currency=currency, total_amount=amount,
        invoice_payload=payload))  # fmt: skip


def paid_message(user, amount=2900):
    upd = msg_update(user)
    msg = upd.message.model_copy(update={"successful_payment": SuccessfulPayment(
        currency="EUR", total_amount=amount, invoice_payload=f"nota:{UNIT}",
        telegram_payment_charge_id="tg_1", provider_payment_charge_id="ch_stripe_1",
        order_info=OrderInfo(email="buyer@example.com"))})  # fmt: skip
    return upd.model_copy(update={"message": msg})


async def _checked(d):
    await d.cb("menu:check")
    await d.cb("k:m:rc")
    await d.say(UNIT)
    await d.say("600")


async def test_invoice_is_sent_instead_of_lead_form(make_dlg, paid_env, fake_session):
    d, _ = make_dlg()
    await _checked(d)
    await d.cb("k:nota")
    inv = fake_session.sent(SendInvoice)[-1]
    assert inv.currency == "EUR" and [p.amount for p in inv.prices] == [2900]
    assert inv.payload == f"nota:{UNIT}" and inv.provider_token == "TEST:provider-token"
    assert inv.need_email and inv.send_email_to_provider
    assert any("Nota simple — 29 €" in m.text for m in fake_session.sent())


async def test_pre_checkout_validation(make_dlg, paid_env, fake_session, dp, bot):
    u = tg_user(80)
    await dp.feed_update(bot, pre_checkout(u))
    await dp.feed_update(bot, pre_checkout(u, amount=100))  # tampered amount
    await dp.feed_update(bot, pre_checkout(u, payload="nota:../../etc"))
    answers = [(a.ok, a.error_message) for a in fake_session.sent(AnswerPreCheckoutQuery)]
    assert answers[0] == (True, None)
    assert answers[1][0] is False and answers[2][0] is False and "Цена изменилась" in answers[1][1]


async def test_successful_payment_creates_paid_lead(
    make_dlg,
    paid_env,
    fake_session,
    dp,
    bot,
    session_factory,
):
    d, _ = make_dlg(uid=81)
    await _checked(d)
    await d.cb("k:nota")
    await dp.feed_update(bot, paid_message(d.user))
    thanks = [m.text for m in fake_session.sent() if m.chat_id == d.user.id]
    assert any("✅ Оплата получена — 29,00 EUR" in x and UNIT in x for x in thanks)
    cards = [c.text for c in fake_session.sent() if c.chat_id == ADMIN_CHAT]
    assert len(cards) == 1 and "Nota simple" in cards[0] and "✅ 29,00 EUR" in cards[0]
    assert "buyer@example.com" in cards[0] and "CL SABINO ARANA 32(B)" in cards[0]
    async with session_factory() as s:
        lead = (await s.scalars(select(Lead))).one()
        assert lead.lead_type == "nota_simple" and lead.data["Stripe charge"] == "ch_stripe_1"
        assert lead.data["Площадь в объявлении, м²"] == 600
        ev = (await s.scalars(select(Event).where(Event.event_type == "payment:nota"))).one()
        assert ev.payload["amount"] == 2900


async def test_without_token_falls_back_to_lead_form(make_dlg, fake_session):
    d, _ = make_dlg(uid=82)
    await _checked(d)
    assert "согласие" in await d.cb("k:nota")
    assert not fake_session.sent(SendInvoice)
