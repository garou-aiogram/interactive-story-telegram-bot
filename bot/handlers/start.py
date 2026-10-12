from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import SUPPORTED_LANGS, config
from bot.db import db
from bot.i18n import LANG_LABELS, t

router = Router()


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=LANG_LABELS[code], callback_data=f"lang:{code}")]
            for code in SUPPORTED_LANGS
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    lang = await db.get_lang(message.from_user.id)
    if lang is None:
        # Регистрируем сразу, иначе юзер не попадёт в рассылку,
        # если бросит бота на экране выбора языка
        await db.set_lang(message.from_user.id, config.default_lang)
        # Первый заход — язык ещё не выбран. Показываем меню на всех языках сразу.
        await message.answer(
            "Choose your language:\nВыбери язык:\nElige tu idioma:",
            reply_markup=lang_keyboard(),
        )
        return
    await message.answer(t("welcome", lang))


@router.message(Command("lang"))
async def cmd_lang(message: Message) -> None:
    lang = await db.get_lang(message.from_user.id)
    await message.answer(t("choose_lang", lang), reply_markup=lang_keyboard())


@router.callback_query(F.data.startswith("lang:"))
async def on_lang(callback: CallbackQuery) -> None:
    code = callback.data.split(":", 1)[1]
    if code not in SUPPORTED_LANGS:
        await callback.answer()
        return

    await db.set_lang(callback.from_user.id, code)
    await callback.message.edit_text(t("lang_set", code))
    await callback.message.answer(t("welcome", code))
    await callback.answer()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    lang = await db.get_lang(message.from_user.id)
    await message.answer(t("help", lang))
