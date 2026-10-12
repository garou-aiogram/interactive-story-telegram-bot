"""Конфигурация. Всё читается из .env — код трогать не нужно."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str

    # --- LLM ---
    llm_base_url: str          # адрес llama-server, напр. http://127.0.0.1:8080/v1
    llm_api_key: str           # llama-server его не проверяет, но поле обязано быть
    llm_model: str             # для llama-server любое непустое значение
    temperature: float
    top_p: float
    max_tokens: int
    request_timeout: int
    stream: bool
    llm_local: bool           # True = llama-server/LM Studio, False = облако
    trust_env: bool           # False = игнорировать системный HTTP_PROXY

    # --- Нагрузка ---
    max_concurrent: int        # одновременных генераций; = --parallel у llama-server

    # --- Контекст ---
    keep_recent_turns: int     # сколько последних ходов шлём дословно
    summarize_after: int       # после скольких ходов сворачиваем старое в summary
    summary_max_tokens: int

    db_path: str
    default_lang: str
    admin_ids: tuple[int, ...]
    broadcast_rate: float      # сообщений в секунду


config = Config(
    bot_token=os.getenv("BOT_TOKEN", ""),
    llm_base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"),
    llm_api_key=os.getenv("LLM_API_KEY", "sk-no-key-required"),
    llm_model=os.getenv("LLM_MODEL", "local-model"),
    temperature=_float("TEMPERATURE", 0.85),
    top_p=_float("TOP_P", 0.92),
    max_tokens=_int("MAX_TOKENS", 700),
    request_timeout=_int("REQUEST_TIMEOUT", 180),
    stream=_bool("STREAM", True),
    llm_local=_bool("LLM_LOCAL", True),
    trust_env=_bool("TRUST_ENV", False),
    max_concurrent=_int("MAX_CONCURRENT", 4),
    keep_recent_turns=_int("KEEP_RECENT_TURNS", 8),
    summarize_after=_int("SUMMARIZE_AFTER", 12),
    summary_max_tokens=_int("SUMMARY_MAX_TOKENS", 400),
    db_path=os.getenv("DB_PATH", "story_bot.db"),
    default_lang=os.getenv("DEFAULT_LANG", "en"),
    admin_ids=tuple(
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
    ),
    broadcast_rate=_float("BROADCAST_RATE", 20.0),
)

SUPPORTED_LANGS = ("en", "ru", "es")

if not config.bot_token:
    raise RuntimeError("BOT_TOKEN не задан. Скопируй .env.example в .env и заполни.")
