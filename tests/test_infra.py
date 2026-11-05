from aiogram.methods import GetChatMember
from aiogram.types import ChatMemberMember, ChatMemberRestricted
from aiogram.types import User as TgUser

from app.bot.middlewares import ThrottlingMiddleware
from app.config import get_settings, load_yaml
from app.integrations.telegram_channel import SubscriptionChecker


def test_configs_load():
    s = get_settings()
    assert s.channel.subscription_cache_seconds == 600
    assert s.calc.require_subscription is False
    assert load_yaml("taxes.yaml")["itp_resale"]["brackets"][0]["rate"] == 0.10
    assert load_yaml("regulation.yaml")["ite_age_threshold_years"] == 45


def test_throttling_window():
    now = [0.0]
    m = ThrottlingMiddleware(2, clock=lambda: now[0])
    assert m.allow(1) and m.allow(1)
    assert not m.allow(1)
    assert m.allow(2)  # other user unaffected
    now[0] = 1.01
    assert m.allow(1)


async def test_subscription_cache(bot, fake_session):
    now = [0.0]
    state = {"member": True}

    def factory(method: GetChatMember):
        u = TgUser(id=method.user_id, is_bot=False, first_name="x")
        if state["member"]:
            return ChatMemberMember(user=u)
        return ChatMemberRestricted(
            user=u,
            is_member=False,
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_topics=False,
            until_date=0,
        )

    fake_session.chat_member_factory = factory
    checker = SubscriptionChecker(bot, "@chan", ttl_seconds=600, clock=lambda: now[0])
    assert await checker.is_subscribed(5)
    state["member"] = False
    now[0] = 599
    assert await checker.is_subscribed(5)  # cached
    assert len(fake_session.sent(GetChatMember)) == 1
    now[0] = 601
    assert not await checker.is_subscribed(5)  # expired -> re-check
    state["member"] = True
    checker.invalidate(5)
    assert await checker.is_subscribed(5)


async def test_subscription_api_error_returns_false(bot, fake_session):
    def boom(_):
        raise RuntimeError("network")

    fake_session.chat_member_factory = boom
    checker = SubscriptionChecker(bot, "@chan")
    assert await checker.is_subscribed(5) is False
