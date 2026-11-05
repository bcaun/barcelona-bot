"""Shared helpers for bot flow tests."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

_ids = itertools.count(1)


def tg_user(uid=7, lang="ru", username="ivan"):
    return TgUser(
        id=uid,
        is_bot=False,
        first_name="Иван",
        last_name="П",
        username=username,
        language_code=lang,
    )


def msg_update(user, text=None, contact=None, chat_id=None, location=None):
    return Update(
        update_id=next(_ids),
        message=Message(
            message_id=next(_ids),
            date=datetime.now(UTC),
            chat=Chat(id=chat_id or user.id, type="private" if not chat_id else "supergroup"),
            from_user=user,
            text=text,
            contact=contact,
            location=location,
        ),
    )


def cb_update(user, data):
    return Update(
        update_id=next(_ids),
        callback_query=CallbackQuery(
            id=str(next(_ids)),
            from_user=user,
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=next(_ids),
                date=datetime.now(UTC),
                chat=Chat(id=user.id, type="private"),
                text="x",
            ),
        ),
    )


async def feed(dp, bot, upd):
    await dp.feed_update(bot, upd)
