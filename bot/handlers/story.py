import asyncio
import logging
import time
from collections import defaultdict

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot.config import config
from bot.context import build_messages, maybe_compress
from bot.db import db
from bot.i18n import t
from bot.llm import LLMError, llm

log = logging.getLogger(__name__)
router = Router()

MAX_INPUT = 1000
TG_LIMIT = 4000          # запас до жёсткого лимита Telegram в 4096
EDIT_INTERVAL = 1.6      # секунд между правками сообщения при стриминге

# Один пользователь — одна генерация за раз. Иначе спам /next валит сервер.
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


class NewStory(StatesGroup):
    waiting_premise = State()


async def _lang(user_id: int) -> str:
    return await db.get_lang(user_id) or config.default_lang


def _split(text: str) -> list[str]:
    """Режет длинный ответ на куски под лимит Telegram, по абзацам."""
    if len(text) <= TG_LIMIT:
        return [text]
    parts, buf = [], ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > TG_LIMIT:
            if buf:
                parts.append(buf.strip())
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        parts.append(buf.strip())
    return parts


async def _generate(message: Message, story, user_action: str | None, lang: str) -> None:
    """Генерирует следующий фрагмент и отправляет его пользователю."""
    messages = await build_messages(story, user_action)

    if config.stream:
        placeholder = await message.answer(t("thinking", lang))
        buffer, last_edit = "", 0.0
        try:
            async for piece in llm.stream(messages):
                buffer += piece
                now = time.monotonic()
                if now - last_edit >= EDIT_INTERVAL and buffer.strip():
                    last_edit = now
                    try:
                        await placeholder.edit_text(buffer[:TG_LIMIT])
                    except Exception:
                        pass  # "message is not modified" и флуд-лимиты глотаем
        except LLMError:
            await placeholder.edit_text(t("llm_error", lang))
            return

        if not buffer.strip():
            await placeholder.edit_text(t("llm_error", lang))
            return

        chunks = _split(buffer.strip())
        try:
            await placeholder.edit_text(chunks[0])
        except Exception:
            await message.answer(chunks[0])
        for extra in chunks[1:]:
            await message.answer(extra)
        reply = buffer.strip()
    else:
        async with ChatActionSender(
            bot=message.bot, chat_id=message.chat.id, action=ChatAction.TYPING
        ):
            try:
                reply = await llm.complete(messages)
            except LLMError:
                await message.answer(t("llm_error", lang))
                return
        for chunk in _split(reply):
            await message.answer(chunk)

    if user_action:
        await db.add_turn(story["id"], "user", user_action)
    await db.add_turn(story["id"], "assistant", reply)

    # Сжимаем контекст фоном, чтобы не задерживать ответ пользователю.
    fresh = await db.get_active_story(message.from_user.id)
    if fresh:
        asyncio.create_task(maybe_compress(fresh))


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    lang = await _lang(message.from_user.id)
    await state.set_state(NewStory.waiting_premise)
    await message.answer(t("ask_premise", lang))


@router.message(NewStory.waiting_premise, Command("skip"))
async def premise_skip(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _begin(message, premise="")


@router.message(NewStory.waiting_premise, F.text)
async def premise_given(message: Message, state: FSMContext) -> None:
    lang = await _lang(message.from_user.id)
    if len(message.text) > MAX_INPUT:
        await message.answer(t("too_long", lang))
        return
    await state.clear()
    await _begin(message, premise=message.text.strip())


async def _begin(message: Message, premise: str) -> None:
    user_id = message.from_user.id
    lang = await _lang(user_id)

    lock = _locks[user_id]
    if lock.locked():
        await message.answer(t("busy", lang))
        return

    async with lock:
        story_id = await db.create_story(user_id, premise, lang)
        story = await db.get_active_story(user_id)
        await _generate(message, story, None, lang)
        log.info("Начата история %s для user=%s lang=%s", story_id, user_id, lang)


@router.message(Command("end"))
async def cmd_end(message: Message) -> None:
    lang = await _lang(message.from_user.id)
    story = await db.get_active_story(message.from_user.id)
    if not story:
        await message.answer(t("no_active_story", lang))
        return
    await db.end_story(story["id"])
    await message.answer(t("story_ended", lang))


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_action(message: Message) -> None:
    """Любой обычный текст = следующий ход игрока."""
    user_id = message.from_user.id
    lang = await _lang(user_id)

    if len(message.text) > MAX_INPUT:
        await message.answer(t("too_long", lang))
        return

    story = await db.get_active_story(user_id)
    if not story:
        await message.answer(t("no_active_story", lang))
        return

    lock = _locks[user_id]
    if lock.locked():
        await message.answer(t("busy", lang))
        return

    async with lock:
        await _generate(message, story, message.text.strip(), lang)
