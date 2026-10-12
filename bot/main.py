import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import config
from bot.db import db
from bot.handlers import admin, start, story
from bot.llm import llm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("story-bot")


async def main() -> None:
    await db.connect()

    if await llm.health():
        log.info("llama-server доступен: %s", config.llm_base_url)
    else:
        log.warning(
            "llama-server НЕ отвечает по %s — бот запустится, но генерация упадёт",
            config.llm_base_url,
        )

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=None),  # проза модели != валидный HTML
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin.router)   # строго до story: у story catch-all на текст
    dp.include_router(start.router)
    dp.include_router(story.router)

    if config.admin_ids:
        log.info("Админы: %s", ", ".join(map(str, config.admin_ids)))
    else:
        log.warning("ADMIN_IDS пуст — /broadcast и /stats недоступны никому")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Бот запущен")
        await dp.start_polling(bot)
    finally:
        await llm.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлен")
