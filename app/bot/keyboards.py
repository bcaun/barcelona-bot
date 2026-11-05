"""Keyboards. Callback data are short ASCII tags."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.i18n import t


class CB:
    CALC = "menu:calc"
    CHECK = "menu:check"
    GAME = "menu:game"
    CONTACT = "menu:contact"
    LANGUAGE = "menu:lang"
    MENU = "menu:main"
    CONSENT = "contact:consent"
    CANCEL = "common:cancel"
    DELETE_CONFIRM = "delete:confirm"
    SET_LANG = "lang:"  # + code


def main_menu(lang: str, channel_url: str) -> InlineKeyboardMarkup:
    def b(key: str, cb: str) -> list[InlineKeyboardButton]:
        return [InlineKeyboardButton(text=t(lang, key), callback_data=cb)]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            b("menu.calc", CB.CALC),
            b("menu.check", CB.CHECK),
            b("menu.game", CB.GAME),
            [InlineKeyboardButton(text=t(lang, "menu.channel"), url=channel_url)],
            b("menu.contact", CB.CONTACT),
            b("menu.language", CB.LANGUAGE),
        ]
    )


def language_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data=CB.SET_LANG + "ru"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data=CB.SET_LANG + "en"),
            ]
        ]
    )


def consent(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "contact.consent_button"), callback_data=CB.CONSENT
                )
            ],
            [InlineKeyboardButton(text=t(lang, "common.cancel"), callback_data=CB.CANCEL)],
        ]
    )


def contact_request(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "contact.send_phone_button"), request_contact=True)],
            [KeyboardButton(text=t(lang, "contact.username_only_button"))],
            [KeyboardButton(text=t(lang, "common.cancel"))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def delete_confirm(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "delete.confirm_button"), callback_data=CB.DELETE_CONFIRM
                )
            ],
            [InlineKeyboardButton(text=t(lang, "common.cancel"), callback_data=CB.CANCEL)],
        ]
    )
