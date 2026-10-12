"""Сборка контекста для интерактивной истории.

Схема: system + summary прошлого + последние N ходов дословно.
Когда ходов становится много, старые сворачиваются одним вызовом модели.
Без этого на длинной истории ты выбьешь окно контекста и модель забудет героев.
"""
import logging

from bot.config import config
from bot.db import db
from bot.llm import LLMError, llm

log = logging.getLogger(__name__)

LANG_NAMES = {"en": "English", "ru": "Russian", "es": "Spanish"}

SYSTEM_PROMPT = """You are the narrator of an interactive story.

LANGUAGE: Write every word in {lang_name}. Never switch language, even if the \
player writes in another one.

STORY PREMISE: {premise}

RULES:
- Write in second person, present tense. The player is the protagonist.
- Each reply is 150-250 words. Never longer.
- Advance the plot with every reply. Something must change.
- Keep characters, names and established facts consistent with what came before.
- Never write the player's dialogue, decisions or thoughts for them.
- End each reply at a moment of choice or tension. Do not list options.
- Output only the story text. No headings, no meta-commentary, no summaries.
"""

OPENING_INSTRUCTION = (
    "Open the story. Establish the setting, the atmosphere and one concrete "
    "immediate problem facing the protagonist. End at the first decision point."
)

SUMMARY_PROMPT = """Condense the story fragment below into a compact record for \
continuity purposes.

Include: named characters and their state, locations, items acquired or lost, \
promises and threats made, unresolved plot threads.
Exclude: prose, atmosphere, description.

Write it in {lang_name}, under 200 words, as dense factual notes.

FRAGMENT:
{fragment}
"""


def system_message(premise: str, lang: str) -> dict:
    return {
        "role": "system",
        "content": SYSTEM_PROMPT.format(
            lang_name=LANG_NAMES.get(lang, "English"),
            premise=premise or "The player chooses their own path.",
        ),
    }


async def build_messages(story, user_action: str | None) -> list[dict]:
    """Формирует messages для запроса к модели."""
    messages = [system_message(story["premise"], story["lang"])]

    if story["summary"]:
        messages.append(
            {
                "role": "system",
                "content": f"STORY SO FAR (continuity notes):\n{story['summary']}",
            }
        )

    recent = await db.get_unsummarized(story["id"])
    recent = recent[-config.keep_recent_turns * 2 :]
    for turn in recent:
        messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": user_action or OPENING_INSTRUCTION})
    return messages


async def maybe_compress(story) -> None:
    """Если ходов накопилось много — сворачивает старые в summary."""
    rows = await db.get_unsummarized(story["id"])
    keep = config.keep_recent_turns * 2

    if len(rows) < config.summarize_after * 2:
        return

    to_fold = rows[:-keep] if len(rows) > keep else []
    if not to_fold:
        return

    fragment = "\n\n".join(
        f"{'PLAYER' if r['role'] == 'user' else 'NARRATOR'}: {r['content']}"
        for r in to_fold
    )
    prompt = SUMMARY_PROMPT.format(
        lang_name=LANG_NAMES.get(story["lang"], "English"), fragment=fragment
    )

    try:
        new_part = await llm.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=config.summary_max_tokens,
        )
    except LLMError:
        log.warning("Не удалось сжать контекст story_id=%s, пропускаю", story["id"])
        return

    merged = (story["summary"] + "\n" + new_part).strip() if story["summary"] else new_part
    await db.update_summary(story["id"], merged)
    await db.mark_summarized([r["id"] for r in to_fold])
    log.info("Сжато %d ходов в story_id=%s", len(to_fold), story["id"])
