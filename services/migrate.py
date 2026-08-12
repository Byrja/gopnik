"""Idempotent migration runner for Пацанский Ход.

Импортируется из services.game_service.ensure_schema().
Также может быть запущен как `python -m services.migrate` для CLI-режима.
"""
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("services.migrate")

# Колонки для users: (name, type, default)
USER_COLUMNS = [
    ("photo_url",      "TEXT",    "''"),
    ("district",       "TEXT",    "''"),
    ("money",          "INTEGER", "0"),
    ("semki",          "INTEGER", "0"),
    ("energy",         "INTEGER", "100"),
    ("energy_max",     "INTEGER", "100"),
    ("last_energy_at", "TEXT",    "'1970-01-01 00:00:00'"),
    ("authority",      "INTEGER", "0"),
    ("strength",       "INTEGER", "1"),
    ("bazar",          "INTEGER", "1"),
    ("stamina",        "INTEGER", "1"),
    ("rating",         "INTEGER", "0"),
    ("wins",           "INTEGER", "0"),
    ("losses",         "INTEGER", "0"),
    ("clan_id",        "INTEGER", "NULL"),
    ("status",         "TEXT",    "'lox'"),
    ("last_nychka_at", "TEXT",    "NULL"),
    ("last_active_at", "TEXT",    "'1970-01-01 00:00:00'"),
    ("last_training_at", "TEXT",  "NULL"),
]


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate_users(conn: sqlite3.Connection) -> int:
    """Add missing columns to users table. Returns number of columns added."""
    added = 0
    for name, typ, default in USER_COLUMNS:
        if not column_exists(conn, "users", name):
            if default == "NULL":
                sql = f"ALTER TABLE users ADD COLUMN {name} {typ}"
            else:
                sql = f"ALTER TABLE users ADD COLUMN {name} {typ} DEFAULT {default}"
            conn.execute(sql)
            log.info("Added column users.%s", name)
            added += 1
    return added


def run_schema_sql(conn: sqlite3.Connection, schema_path: Path) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(sql)
    log.info("Applied schema.sql")


def ensure_boss_schema(conn: sqlite3.Connection) -> None:
    """Идемпотентная миграция для PVE-боссов."""
    from services import bosses
    bosses.ensure_tables(conn)
    log.info("Boss schema ensured")


def ensure_schema(db_path: str | Path) -> None:
    """Idempotent entrypoint — вызывается из бота/веб-аппа при старте."""
    here = Path(__file__).resolve().parent
    db_p = Path(db_path).resolve()
    schema_path = here / "schema.sql"

    if not db_p.exists():
        raise FileNotFoundError(f"DB not found: {db_p}")
    if not schema_path.exists():
        raise FileNotFoundError(f"schema.sql not found: {schema_path}")

    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # Сначала CREATE TABLE IF NOT EXISTS (включая users)
        run_schema_sql(conn, schema_path)
        # Потом ALTER TABLE — добавляем новые колонки
        added = migrate_users(conn)
        if added:
            log.info("users table: +%d columns", added)
        # PVE боссы
        ensure_boss_schema(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    # CLI: миграция относительно текущего рабочего каталога
    # для services/migrate.py → ../data/gop.db
    here = Path(__file__).resolve().parent
    project_root = here.parent
    db_path = project_root / "data" / "gop.db"

    try:
        ensure_schema(db_path)
        log.info("Migration complete: %s", db_path)
        return 0
    except Exception as e:
        log.exception("Migration failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
