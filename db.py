"""Database layer for Гопник-бот."""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("gop-bot.db")


class GopDB:
    def __init__(self, db_path: str = "data/gop.db"):
        self.db_path = db_path
        self.conn = None

    def _get_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
        return self.conn

    def init_db(self):
        conn = self._get_conn()
        # Ensure message_count column exists (migration for existing DBs)
        try:
            conn.execute("ALTER TABLE escalation_state ADD COLUMN message_count INTEGER DEFAULT 0")
            conn.commit()
            logger.info("Added message_count column to escalation_state")
        except Exception:
            pass  # Column already exists

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                last_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS gop_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                caller_id INTEGER NOT NULL,
                victim_id INTEGER,
                request_text TEXT DEFAULT '',
                response_text TEXT DEFAULT '',
                escalation_level INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS gop_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                -- 'user' = victim reply, 'gop' = bot response
                text TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_gop_messages_chat_user
                ON gop_messages(chat_id, user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS gop_stats (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                times_called INTEGER DEFAULT 0,
                times_gopped INTEGER DEFAULT 0,
                respect_earned INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS gop_blacklist (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS escalation_state (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                level INTEGER DEFAULT 1,
                message_count INTEGER DEFAULT 0,
                last_interaction TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (chat_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS nicknames (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                nickname TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS bot_cooldowns (
                chat_id INTEGER PRIMARY KEY,
                last_response_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS styles (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                style TEXT DEFAULT 'gopnik',
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                achievement_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                unlocked_at TEXT DEFAULT (datetime('now')),
                UNIQUE(achievement_id, user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS gop_bot_messages (
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                target_user_id INTEGER,
                escalation_level INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (message_id, chat_id)
            );
        """)
        conn.commit()

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------
    def get_or_create_user(self, tg_id: int, username: str = "", first_name: str = "", last_name: str = "") -> dict:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        if row:
            # Update name if changed
            if username or first_name:
                conn.execute(
                    "UPDATE users SET username = COALESCE(NULLIF(?, ''), username), first_name = COALESCE(NULLIF(?, ''), first_name), last_name = COALESCE(NULLIF(?, ''), last_name) WHERE tg_id = ?",
                    (username, first_name, last_name, tg_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
            return dict(row)

        conn.execute(
            "INSERT INTO users (tg_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
            (tg_id, username, first_name, last_name),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone())

    def find_user_by_username(self, username: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
            (username.lstrip("@"),),
        ).fetchone()
        return dict(row) if row else None

    # -----------------------------------------------------------------------
    # Messages (context history)
    # -----------------------------------------------------------------------
    def add_message(self, chat_id: int, user_id: int, role: str, text: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO gop_messages (chat_id, user_id, role, text) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, role, text),
        )
        # Keep only last 50 messages per (chat_id, user_id)
        conn.execute("""
            DELETE FROM gop_messages
            WHERE (chat_id, user_id, id) NOT IN (
                SELECT chat_id, user_id, id FROM gop_messages m2
                WHERE m2.chat_id = ? AND m2.user_id = ?
                ORDER BY m2.created_at DESC
                LIMIT 50
            ) AND chat_id = ? AND user_id = ?
        """, (chat_id, user_id, chat_id, user_id))
        conn.commit()

    def get_recent_messages(self, chat_id: int, user_id: int, limit: int = 50) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT role, text, created_at FROM gop_messages WHERE chat_id = ? AND user_id = ? ORDER BY created_at ASC LIMIT ?",
            (chat_id, user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -----------------------------------------------------------------------
    # Escalation
    # -----------------------------------------------------------------------
    def get_escalation(self, chat_id: int, user_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM escalation_state WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        if row:
            return dict(row)
        return None

    def update_escalation(self, chat_id: int, user_id: int, level: int, message_count: int = None):
        conn = self._get_conn()
        if message_count is None:
            # Just update level
            conn.execute("""
                INSERT INTO escalation_state (chat_id, user_id, level, last_interaction)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    level = excluded.level,
                    last_interaction = datetime('now')
            """, (chat_id, user_id, level))
        else:
            # Update both level and message_count
            conn.execute("""
                INSERT INTO escalation_state (chat_id, user_id, level, message_count, last_interaction)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    level = excluded.level,
                    message_count = excluded.message_count,
                    last_interaction = datetime('now')
            """, (chat_id, user_id, level, message_count))
        conn.commit()

    def reset_escalation(self, chat_id: int, user_id: int):
        """Reset escalation after timeout (called periodically)."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM escalation_state WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        conn.commit()

    # -----------------------------------------------------------------------
    # Nicknames
    # -----------------------------------------------------------------------
    def get_nickname(self, user_id: int, chat_id: int) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT nickname FROM nicknames WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        return row["nickname"] if row else None

    def update_nickname(self, user_id: int, chat_id: int, nickname: Optional[str]):
        conn = self._get_conn()
        if nickname is None:
            conn.execute(
                "DELETE FROM nicknames WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
        else:
            conn.execute("""
                INSERT INTO nicknames (user_id, chat_id, nickname) VALUES (?, ?, ?)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET nickname = excluded.nickname
            """, (user_id, chat_id, nickname))
        conn.commit()

    # -----------------------------------------------------------------------
    # Stats
    # -----------------------------------------------------------------------
    def increment_called(self, user_id: int, chat_id: int):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO gop_stats (user_id, chat_id, times_called) VALUES (?, ?, 1)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET times_called = times_called + 1
        """, (user_id, chat_id))
        conn.commit()

    def increment_gopped(self, user_id: int, chat_id: int):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO gop_stats (user_id, chat_id, times_gopped) VALUES (?, ?, 1)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET times_gopped = times_gopped + 1
        """, (user_id, chat_id))
        conn.commit()

    def increment_respect(self, user_id: int, chat_id: int):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO gop_stats (user_id, chat_id, respect_earned) VALUES (?, ?, 1)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET respect_earned = respect_earned + 1
        """, (user_id, chat_id))
        conn.commit()

    def get_user_stats(self, user_id: int, chat_id: int) -> dict:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM gop_stats WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        if row:
            return dict(row)
        return {"times_called": 0, "times_gopped": 0, "respect_earned": 0}

    # -----------------------------------------------------------------------
    # Blacklist
    # -----------------------------------------------------------------------
    def is_blacklisted(self, user_id: int, chat_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM gop_blacklist WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        return row is not None

    def is_blacklisted_global(self, user_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM gop_blacklist WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row is not None

    def add_to_blacklist(self, user_id: int, chat_id: int):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO gop_blacklist (user_id, chat_id) VALUES (?, ?)",
            (user_id, chat_id),
        )
        conn.commit()

    def remove_from_blacklist(self, user_id: int, chat_id: int):
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM gop_blacklist WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        conn.commit()

    # -----------------------------------------------------------------------
    # Bot messages (for reply tracking)
    # -----------------------------------------------------------------------
    def save_gop_message(self, message_id: int, chat_id: int, target_user_id: int, level: int = 1):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO gop_bot_messages (message_id, chat_id, target_user_id, escalation_level) VALUES (?, ?, ?, ?)",
            (message_id, chat_id, target_user_id, level),
        )
        conn.commit()

    def get_gop_message(self, message_id: int, chat_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM gop_bot_messages WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id),
        ).fetchone()
        return dict(row) if row else None

    # -----------------------------------------------------------------------
    # Styles
    # -----------------------------------------------------------------------
    def get_style(self, user_id: int, chat_id: int) -> str:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT style FROM styles WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        return row["style"] if row else "gopnik"

    def set_style(self, user_id: int, chat_id: int, style: str):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO styles (user_id, chat_id, style) VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET style = excluded.style
        """, (user_id, chat_id, style))
        conn.commit()

    # -----------------------------------------------------------------------
    # Achievements
    # -----------------------------------------------------------------------
    def unlock_achievement(self, achievement_id: str, user_id: int, chat_id: int) -> bool:
        """Returns True if newly unlocked, False if already had it."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO achievements (achievement_id, user_id, chat_id) VALUES (?, ?, ?)",
                (achievement_id, user_id, chat_id),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    # -----------------------------------------------------------------------
    # Bot cooldowns — prevent flooding
    # -----------------------------------------------------------------------
    def get_cooldown(self, chat_id: int) -> Optional[datetime]:
        """Get last response timestamp for this chat. Returns None if no cooldown record."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT last_response_at FROM bot_cooldowns WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if row:
            try:
                return datetime.fromisoformat(row["last_response_at"])
            except Exception:
                return None
        return None

    def set_cooldown(self, chat_id: int):
        """Mark now as the last response time for this chat."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO bot_cooldowns (chat_id, last_response_at)
            VALUES (?, datetime('now'))
            ON CONFLICT(chat_id) DO UPDATE SET
                last_response_at = excluded.last_response_at
        """, (chat_id,))
        conn.commit()

    def get_user_achievements(self, user_id: int, chat_id: int) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM achievements WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchall()
        return [dict(r) for r in rows]