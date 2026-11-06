"""Paid nota simple via Telegram Payments (provider: Stripe).

Flow: «Nota simple» → offer + invoice → pre_checkout_query (validated) → successful_payment
→ lead `nota_simple` marked as paid → admin card. Legal basis: performance of the order
(GDPR art. 6(1)(b)); the offer text links the privacy policy.
"""

from __future__ import annotations

import html
import re

import structlog
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.start import send_menu
from app.bot.notify import send_lead_card
from app.config import Env, Settings
from app.core import cadastre as cd
from app.core import repo
from app.core.models import User, utcnow
from app.i18n import t

router = Router(name="payments")
log = structlog.get_logger(__name__)

PAYLOAD_RE = re.compile(r"nota:([0-9A-ZÑ]{14}(?:[0-9A-ZÑ]{6})?)")


def amount_minor(settings: Settings) -> int:
    return settings.nota_simple.price_eur * 100


async def send_nota_invoice(
    message: Message, p: cd.Property, lang: str, settings: Settings, env: Env
) -> None:
    """Offer text + Telegram invoice (card / Apple Pay / Google Pay through Stripe)."""
    assert env.payment_provider_token is not None
    price = settings.nota_simple.price_eur
    address = p.address.split(" 0")[0] if p.address else p.rc
    await message.answer(
        t(lang, "pay.offer", price=price, address=html.escape(address),
          privacy_url=settings.legal.privacy_policy_url),
        disable_web_page_preview=True,
    )  # fmt: skip
    await message.answer_invoice(
        title=t(lang, "pay.title")[:32],
        description=t(lang, "pay.description", rc=p.rc, address=address)[:255],
        payload=f"nota:{p.rc}",
        provider_token=env.payment_provider_token.get_secret_value(),
        currency=settings.nota_simple.currency,
        prices=[LabeledPrice(label=t(lang, "pay.title")[:32], amount=amount_minor(settings))],
        need_email=True,
        send_email_to_provider=True,  # Stripe sends the receipt
    )


def payment_link(settings: Settings, lead_id: int) -> str:
    """Stripe Payment Link + our order number (shown in the Stripe payment as Client reference)."""
    from urllib.parse import urlencode

    url = settings.nota_simple.payment_link_url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode({'client_reference_id': f'bcnbot-lead-{lead_id}'})}"


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, lang: str, settings: Settings) -> None:
    ok = (
        bool(PAYLOAD_RE.fullmatch(query.invoice_payload))
        and query.currency == settings.nota_simple.currency
        and query.total_amount == amount_minor(settings)
    )
    if ok:
        await query.answer(ok=True)
    else:  # price changed since the invoice was sent, or a forged payload
        log.warning("pre_checkout_rejected", payload=query.invoice_payload[:40],
                    amount=query.total_amount)  # fmt: skip
        await query.answer(ok=False, error_message=t(lang, "pay.outdated"))


@router.message(F.successful_payment)
async def successful_payment(
    message: Message,
    user: User,
    lang: str,
    settings: Settings,
    env: Env,
    session: AsyncSession,
    bot: Bot,
    state: FSMContext,
) -> None:
    sp = message.successful_payment
    if sp is None:
        return
    m = PAYLOAD_RE.fullmatch(sp.invoice_payload)
    rc = m.group(1) if m else sp.invoice_payload[:20]
    admin_lang = settings.admin.language
    amount = f"{sp.total_amount / 100:.2f} {sp.currency}".replace(".", ",")
    data = {
        t(admin_lang, "check.lead.rc"): rc,
        t(admin_lang, "pay.lead.paid"): f"✅ {amount}",
        t(admin_lang, "pay.lead.email"): (sp.order_info.email if sp.order_info else None) or "—",
        t(admin_lang, "pay.lead.charge"): sp.provider_payment_charge_id,
        "telegram_charge_id": sp.telegram_payment_charge_id,
    }
    # add address / listing area from the check, if it is the same property
    from app.bot.handlers.check import last_check  # local import: check imports this module

    check = await last_check(state, session, user)
    if check.get("property", {}).get("rc") == rc:
        prop = check["property"]
        data[t(admin_lang, "check.lead.address")] = prop.get("address")
        if check.get("listing_area"):
            data[t(admin_lang, "check.lead.listing_area")] = check["listing_area"]
    tg = message.from_user
    lead = await repo.create_lead(
        session, user, "nota_simple", name=tg.full_name if tg else None, phone=None,
        data=data, consent_at=utcnow(),
    )  # fmt: skip
    await repo.log_event(
        session,
        user.id,
        "payment:nota",
        {"lead_id": lead.id, "amount": sp.total_amount, "currency": sp.currency},
        source=lead.source,
    )
    lead.admin_message_id = await send_lead_card(bot, env.admin_chat_id, lead, user, admin_lang)
    log.info("nota_paid", lead_id=lead.id, amount=sp.total_amount)
    await message.answer(t(lang, "pay.thanks", amount=amount, rc=rc, lead_id=lead.id))
    await send_menu(message, lang, settings)
