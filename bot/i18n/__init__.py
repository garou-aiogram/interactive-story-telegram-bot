"""Простой i18n: JSON-словарь на язык, фолбэк на английский."""
import json
from pathlib import Path

from bot.config import SUPPORTED_LANGS, config

_DIR = Path(__file__).parent
_STRINGS: dict[str, dict[str, str]] = {}

for _lang in SUPPORTED_LANGS:
    with open(_DIR / f"{_lang}.json", encoding="utf-8") as f:
        _STRINGS[_lang] = json.load(f)

LANG_LABELS = {"en": "🇬🇧 English", "ru": "🇷🇺 Русский", "es": "🇪🇸 Español"}


def t(key: str, lang: str | None = None, **kwargs) -> str:
    lang = lang if lang in _STRINGS else config.default_lang
    text = _STRINGS.get(lang, {}).get(key) or _STRINGS["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text
