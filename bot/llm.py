"""Клиент к LLM через официальный SDK openai.

Работает с любым OpenAI-совместимым бэкендом:
  - llama-server / LM Studio (локально)
  - Gemini через OpenAI-совместимый эндпоинт
  - Groq, OpenRouter и прочие

Переключение — только через .env, код не трогается.
"""
import asyncio
import logging
import random
from collections.abc import AsyncIterator

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    RateLimitError,
)

from bot.config import config

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            timeout=config.request_timeout,
            max_retries=0,  # ретраим сами — нужен контроль над backoff
            # trust_env=False обязателен: без него системный HTTP_PROXY
            # перехватывает даже запросы на 127.0.0.1 и отдаёт 502.
            http_client=httpx.AsyncClient(trust_env=config.trust_env),
        )
        # Ограничивает одновременные генерации.
        # Локально держи <= --parallel у llama-server.
        # На облаке защищает от упирания в RPM провайдера.
        self._sem = asyncio.Semaphore(config.max_concurrent)

    async def close(self) -> None:
        await self._client.close()

    def _kwargs(self, messages: list[dict], *, stream: bool, max_tokens: int | None) -> dict:
        kw = {
            "model": config.llm_model,
            "messages": messages,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": max_tokens or config.max_tokens,
            "stream": stream,
        }
        if config.llm_local:
            # Специфично для llama.cpp: гасим reasoning-режим у Gemma 4 и подобных.
            # Облачные провайдеры на незнакомое поле отвечают 400, поэтому только локально.
            kw["extra_body"] = {"chat_template_kwargs": {"thinking": False}}
        return kw

    async def _with_retry(self, coro_factory, attempts: int = 3):
        """Ретраи с экспоненциальным backoff и джиттером.

        429 на облачных провайдерах — норма, а не авария. Без ретраев
        юзер получает ошибку там, где хватило бы паузы в секунду.
        """
        delay = 1.0
        for attempt in range(1, attempts + 1):
            try:
                return await coro_factory()
            except (RateLimitError, APIConnectionError) as e:
                if attempt == attempts:
                    raise LLMError(str(e)) from e
                sleep = delay + random.uniform(0, 0.5)
                log.warning("LLM %s, попытка %d/%d, пауза %.1fs",
                            type(e).__name__, attempt, attempts, sleep)
                await asyncio.sleep(sleep)
                delay *= 2
            except APIStatusError as e:
                # 400/401/404 ретраить бессмысленно — это ошибка конфига
                raise LLMError(f"HTTP {e.status_code}: {e.message}") from e

    async def complete(self, messages: list[dict], *, max_tokens: int | None = None) -> str:
        async with self._sem:
            async def call():
                return await self._client.chat.completions.create(
                    **self._kwargs(messages, stream=False, max_tokens=max_tokens)
                )

            resp = await self._with_retry(call)
            content = resp.choices[0].message.content
            if not content:
                raise LLMError("Модель вернула пустой ответ")
            return content.strip()

    async def stream(
        self, messages: list[dict], *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        """SDK сам разбирает SSE — руками парсить 'data: ' больше не нужно."""
        async with self._sem:
            async def call():
                return await self._client.chat.completions.create(
                    **self._kwargs(messages, stream=True, max_tokens=max_tokens)
                )

            stream = await self._with_retry(call)
            try:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    piece = chunk.choices[0].delta.content
                    if piece:
                        yield piece
            except (APIConnectionError, APIStatusError) as e:
                # Обрыв посреди стрима не ретраим: часть текста уже ушла юзеру
                raise LLMError(str(e)) from e

    async def health(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception as e:
            log.debug("health check failed: %s", e)
            return False


llm = LLMClient()
