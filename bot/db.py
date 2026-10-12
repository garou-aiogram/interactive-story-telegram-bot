"""SQLite через aiosqlite. Состояние переживает рестарт бота."""
import aiosqlite

from bot.config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    lang        TEXT NOT NULL DEFAULT 'en',
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    title       TEXT,
    premise     TEXT,
    summary     TEXT NOT NULL DEFAULT '',
    lang        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL,
    role        TEXT NOT NULL,          -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    summarized  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (story_id) REFERENCES stories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_turns_story ON turns(story_id, id);
CREATE INDEX IF NOT EXISTS idx_stories_user ON stories(user_id, is_active);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Догоняет схему для БД, созданных до появления новых колонок."""
        cur = await self._conn.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in await cur.fetchall()}
        if "is_active" not in cols:
            await self._conn.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не был вызван")
        return self._conn

    # ---------- users ----------

    async def get_lang(self, user_id: int) -> str | None:
        cur = await self.conn.execute(
            "SELECT lang FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["lang"] if row else None

    async def set_lang(self, user_id: int, lang: str) -> None:
        await self.conn.execute(
            "INSERT INTO users (user_id, lang) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang",
            (user_id, lang),
        )
        await self.conn.commit()

    async def all_active_user_ids(self) -> list[int]:
        cur = await self.conn.execute(
            "SELECT user_id FROM users WHERE is_active = 1 ORDER BY user_id"
        )
        return [row["user_id"] for row in await cur.fetchall()]

    async def set_active(self, user_id: int, active: bool) -> None:
        await self.conn.execute(
            "UPDATE users SET is_active = ? WHERE user_id = ?", (int(active), user_id)
        )
        await self.conn.commit()

    async def user_stats(self) -> dict:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(is_active) AS active FROM users"
        )
        row = await cur.fetchone()
        return {"total": row["total"] or 0, "active": row["active"] or 0}

    # ---------- stories ----------

    async def get_active_story(self, user_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM stories WHERE user_id = ? AND is_active = 1 "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return await cur.fetchone()

    async def create_story(self, user_id: int, premise: str, lang: str) -> int:
        await self.conn.execute(
            "UPDATE stories SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        cur = await self.conn.execute(
            "INSERT INTO stories (user_id, premise, lang) VALUES (?, ?, ?)",
            (user_id, premise, lang),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def end_story(self, story_id: int) -> None:
        await self.conn.execute(
            "UPDATE stories SET is_active = 0 WHERE id = ?", (story_id,)
        )
        await self.conn.commit()

    async def update_summary(self, story_id: int, summary: str) -> None:
        await self.conn.execute(
            "UPDATE stories SET summary = ? WHERE id = ?", (summary, story_id)
        )
        await self.conn.commit()

    # ---------- turns ----------

    async def add_turn(self, story_id: int, role: str, content: str) -> None:
        await self.conn.execute(
            "INSERT INTO turns (story_id, role, content) VALUES (?, ?, ?)",
            (story_id, role, content),
        )
        await self.conn.commit()

    async def get_unsummarized(self, story_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM turns WHERE story_id = ? AND summarized = 0 ORDER BY id",
            (story_id,),
        )
        return list(await cur.fetchall())

    async def mark_summarized(self, turn_ids: list[int]) -> None:
        if not turn_ids:
            return
        placeholders = ",".join("?" * len(turn_ids))
        await self.conn.execute(
            f"UPDATE turns SET summarized = 1 WHERE id IN ({placeholders})", turn_ids
        )
        await self.conn.commit()

    async def count_turns(self, story_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS c FROM turns WHERE story_id = ?", (story_id,)
        )
        row = await cur.fetchone()
        return row["c"]


db = Database(config.db_path)
