"""Админка: рассылка всем пользователям.

Используется copy_message — он переносит любой тип контента (текст, фото,
видео, гифки, кружки, стикеры, документы) без перезаливки на сервер Telegram.
Разбирать типы сообщений вручную не нужно.
"""
import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import config
from bot.db import db

log = logging.getLogger(__name__)
router = Router()


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id in config.admin_ids


# Фильтр вешается на роутер целиком: не-админ вообще не попадёт в эти хендлеры
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class Broadcast(StatesGroup):
    waiting_content = State()
    confirming = State()


_running = False  # одна рассылка за раз


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="bc:go"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="bc:no"),
            ]
        ]
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    s = await db.user_stats()
    await message.answer(
        f"Всего пользователей: {s['total']}\n"
        f"Активных (не заблокировали бота): {s['active']}"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if _running:
        await message.answer("Рассылка уже идёт. Дождись её завершения.")
        return
    await state.set_state(Broadcast.waiting_content)
    await message.answer(
        "Пришли сообщение для рассылки.\n\n"
        "Подойдёт что угодно: текст, фото, видео, гифка, кружок, документ, "
        "стикер — с подписью или без.\n\n"
        "/cancel — отмена."
    )


@router.message(Command("cancel"), Broadcast.waiting_content)
@router.message(Command("cancel"), Broadcast.confirming)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(Broadcast.waiting_content)
async def got_content(message: Message, state: FSMContext) -> None:
    # Запоминаем координаты сообщения, а не его содержимое.
    # copy_message потом просто скопирует его каждому получателю.
    await state.update_data(
        from_chat_id=message.chat.id, message_id=message.message_id
    )
    await state.set_state(Broadcast.confirming)

    stats = await db.user_stats()
    await message.answer(
        f"Сообщение выше уйдёт {stats['active']} пользователям.\n\nОтправляем?",
        reply_markup=_confirm_kb(),
    )


@router.callback_query(F.data == "bc:no", Broadcast.confirming)
async def bc_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data == "bc:go", Broadcast.confirming)
async def bc_go(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_text("Рассылка запущена...")
    await callback.answer()

    # Фоном, иначе бот перестанет отвечать другим на время рассылки
    asyncio.create_task(
        _run_broadcast(
            bot=bot,
            admin_chat=callback.message.chat.id,
            from_chat_id=data["from_chat_id"],
            message_id=data["message_id"],
        )
    )


async def _run_broadcast(
    bot: Bot, admin_chat: int, from_chat_id: int, message_id: int
) -> None:
    global _running
    _running = True

    user_ids = await db.all_active_user_ids()
    total = len(user_ids)
    sent = blocked = failed = 0
    started = time.monotonic()

    # Telegram режет примерно на 30 сообщениях в секунду.
    # Держим ниже потолка, иначе словим массовый 429.
    delay = 1.0 / max(config.broadcast_rate, 1.0)

    progress = await bot.send_message(admin_chat, f"Отправлено 0 / {total}")

    for i, user_id in enumerate(user_ids, 1):
        try:
            await bot.copy_message(
                chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id
            )
            sent += 1

        except TelegramRetryAfter as e:
            # Сервер сам сказал, сколько ждать. Ждём и повторяем этого юзера.
            log.warning("Flood limit, пауза %s сек", e.retry_after)
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id
                )
                sent += 1
            except Exception:
                failed += 1

        except TelegramForbiddenError:
            # Юзер заблокировал бота или удалил аккаунт.
            # Помечаем неактивным, чтобы не долбиться в него каждую рассылку.
            blocked += 1
            await db.set_active(user_id, False)

        except TelegramBadRequest as e:
            failed += 1
            log.warning("Не доставлено user=%s: %s", user_id, e.message)

        except Exception as e:
            failed += 1
            log.exception("Ошибка рассылки user=%s: %s", user_id, e)

        if i % 25 == 0:
            try:
                await progress.edit_text(f"Отправлено {i} / {total}")
            except Exception:
                pass

        await asyncio.sleep(delay)

    _running = False
    elapsed = int(time.monotonic() - started)

    try:
        await progress.edit_text(
            f"Рассылка завершена за {elapsed} сек\n\n"
            f"Доставлено: {sent}\n"
            f"Заблокировали бота: {blocked}\n"
            f"Прочие ошибки: {failed}"
        )
    except Exception:
        await bot.send_message(admin_chat, f"Готово. Доставлено {sent} из {total}.")

    log.info("Рассылка: sent=%s blocked=%s failed=%s", sent, blocked, failed)
