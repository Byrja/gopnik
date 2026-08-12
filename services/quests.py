"""Quest engine for Пацанский Ход — квесты от Шрупа.

Квесты бывают трёх типов:
- single: разовые, выполнить один раз
- daily: ежедневные (сбрасываются раз в сутки)
- recurring: повторяющиеся (не истекают, пока не закроешь)

Прогресс хранится в user_quest_progress.
Статус — в user_active_quests / user_quests.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("gop-bot.quest")

# ---------------------------------------------------------------------------
# Seed-данные квестов (Шруп)
# ---------------------------------------------------------------------------

QUEST_DEFINITIONS = [
    # --- Single: первые шаги ---
    {
        "code": "first_work",
        "title": "Первая работа",
        "description": "Сходи на работу. Первый рубль — самый тяжёлый.",
        "type": "single",
        "xp_reward": 50,
        "money_reward": 20,
        "authority_reward": 1,
    },
    {
        "code": "first_gop",
        "title": "Первая гопка",
        "description": "Гопни кого-нибудь. Первый бой — самый волнительный.",
        "type": "single",
        "xp_reward": 100,
        "money_reward": 30,
        "authority_reward": 2,
    },
    {
        "code": "first_win",
        "title": "Первая победа",
        "description": "Победи в PvP. Ты теперь не лох, а гопник.",
        "type": "single",
        "xp_reward": 150,
        "money_reward": 50,
        "authority_reward": 3,
    },
    {
        "code": "first_mutka",
        "title": "Первая мутка",
        "description": "Завершится успешно? Или слил? Попробуй.",
        "type": "single",
        "xp_reward": 80,
        "money_reward": 40,
        "authority_reward": 2,
    },
    {
        "code": "first_nychka",
        "title": "Первая нычка",
        "description": "Загляни в подъезд — там лежат.",
        "type": "single",
        "xp_reward": 40,
        "money_reward": 30,
        "authority_reward": 0,
    },
    {
        "code": "semka_hoarder",
        "title": "Семечко-магнат",
        "description": "Накопи 10 семок. Семки — валюта улиц.",
        "type": "single",
        "xp_reward": 60,
        "money_reward": 25,
        "authority_reward": 1,
    },
    {
        "code": "energy_saver",
        "title": "Экономный пацан",
        "description": "Доживи до полного бара энергии. Не трать зря.",
        "type": "single",
        "xp_reward": 30,
        "money_reward": 15,
        "authority_reward": 0,
    },
    {
        "code": "turnik_regular",
        "title": "Турник-мен",
        "description": "Сделай 5 тренировок на турниках. Сила — это база.",
        "type": "single",
        "xp_reward": 100,
        "money_reward": 20,
        "authority_reward": 2,
    },
    {
        "code": "bazar_master",
        "title": "Базарный дед",
        "description": "Прокачай базар до 5. Язык — это оружие.",
        "type": "single",
        "xp_reward": 100,
        "money_reward": 20,
        "authority_reward": 2,
    },
    {
        "code": "first_1000",
        "title": "Первая тысяча",
        "description": "Накопи 1000 рублей. Тысяча — это уже статус.",
        "type": "single",
        "xp_reward": 120,
        "money_reward": 100,
        "authority_reward": 3,
    },

    # --- Daily ---
    {
        "code": "daily_work",
        "title": "Дневная работа",
        "description": "Сходи на работу сегодня.",
        "type": "daily",
        "target": 1,
        "progress_type": "work_count",
        "xp_reward": 30,
        "money_reward": 15,
        "authority_reward": 0,
    },
    {
        "code": "daily_gop",
        "title": "Дневная гопка",
        "description": "Гопни кого-нибудь сегодня.",
        "type": "daily",
        "target": 1,
        "progress_type": "gop_count",
        "xp_reward": 50,
        "money_reward": 25,
        "authority_reward": 1,
    },
    {
        "code": "daily_turnik",
        "title": "Турники на завтрак",
        "description": "Потренируйся на турниках сегодня.",
        "type": "daily",
        "target": 2,
        "progress_type": "turnik_count",
        "xp_reward": 40,
        "money_reward": 10,
        "authority_reward": 0,
    },
    {
        "code": "daily_bazar",
        "title": "Базарный день",
        "description": "Прокачай базар сегодня.",
        "type": "daily",
        "target": 2,
        "progress_type": "bazar_count",
        "xp_reward": 40,
        "money_reward": 10,
        "authority_reward": 0,
    },

    # --- Recurring ---
    {
        "code": "gop_warrior",
        "title": "Гоп-воин",
        "description": "Выиграй 10 PvP-боёв. Ты — гроза района.",
        "type": "recurring",
        "target": 10,
        "progress_type": "total_wins",
        "xp_reward": 300,
        "money_reward": 200,
        "authority_reward": 10,
    },
    {
        "code": "street_king",
        "title": "Король района",
        "description": "Достигни 200 рейтинга. Ты — авторитет.",
        "type": "recurring",
        "target": 200,
        "progress_type": "rating_200",
        "xp_reward": 500,
        "money_reward": 500,
        "authority_reward": 20,
    },
    {
        "code": "rich_patsan",
        "title": "Богатый пацан",
        "description": "Накопи 5000 рублей. Деньги — это власть.",
        "type": "recurring",
        "target": 5000,
        "progress_type": "money_5000",
        "xp_reward": 400,
        "money_reward": 300,
        "authority_reward": 15,
    },
]

# ---------------------------------------------------------------------------
# Закрытые / будущие квесты — видны в UI но недоступны
# ---------------------------------------------------------------------------

LOCKED_QUESTS = [
    {
        "code": "weekly_rich",
        "title": "Неделя бабла",
        "description": "Заработай 3000₽ за неделю.",
        "type": "weekly",
        "unlock_info": "Скоро",
        "xp_reward": 200,
        "money_reward": 500,
        "authority_reward": 8,
    },
    {
        "code": "monthly_gop",
        "title": "Месяц гопника",
        "description": "Сделай 50 гоп-ходов за месяц.",
        "type": "monthly",
        "unlock_info": "Откроется скоро",
        "xp_reward": 1000,
        "money_reward": 1000,
        "authority_reward": 50,
    },
    {
        "code": "clan_boss",
        "title": "Шеф братвы",
        "description": "Стань лидером клана с 10+ участниками.",
        "type": "clan",
        "unlock_info": "Откроется когда кланы заработают",
        "xp_reward": 800,
        "money_reward": 600,
        "authority_reward": 30,
    },
    {
        "code": "semka_king",
        "title": "Король семок",
        "description": "Обменяй 50 семок на что-то стоящее.",
        "type": "single",
        "unlock_info": "Скоро — добавим обмен",
        "xp_reward": 150,
        "money_reward": 200,
        "authority_reward": 5,
    },
    {
        "code": "turnik_master",
        "title": "Мастер подтягиваний",
        "description": "Сделай 100 подтягиваний за всё время.",
        "type": "recurring",
        "unlock_info": "Откроется скоро",
        "xp_reward": 300,
        "money_reward": 100,
        "authority_reward": 15,
    },
    {
        "code": "district_conqueror",
        "title": "Завоеватель районов",
        "description": "Побывай во всех районах.",
        "type": "single",
        "unlock_info": "Скоро — добавим районы",
        "xp_reward": 500,
        "money_reward": 300,
        "authority_reward": 20,
    },
]


def get_locked_quests(conn: sqlite3.Connection) -> list[dict]:
    """Возвращает список закрытых/будущих квестов для UI."""
    return LOCKED_QUESTS


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class QuestInfo:
    code: str
    title: str
    description: str
    quest_type: str  # single | daily | recurring
    xp_reward: int
    money_reward: int
    authority_reward: int
    target: int = 1
    progress_type: str = ""

    @property
    def is_single(self) -> bool:
        return self.quest_type == "single"

    @property
    def is_daily(self) -> bool:
        return self.quest_type == "daily"

    @property
    def is_recurring(self) -> bool:
        return self.quest_type == "recurring"


@dataclass
class QuestProgress:
    quest: QuestInfo
    current: int = 0
    target: int = 1
    completed: bool = False
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    @property
    def progress_pct(self) -> int:
        if self.target <= 0:
            return 100
        return min(100, int(self.current / self.target * 100))

    def __str__(self) -> str:
        bar_len = 10
        filled = int(self.progress_pct / 10)
        bar = "█" * filled + "░" * (bar_len - filled)
        return f"[{bar}] {self.current}/{self.target}"


@dataclass
class QuestReward:
    xp: int
    money: int
    authority: int


# ---------------------------------------------------------------------------
# Progress trackers — функции, которые возвращают текущий прогресс
# ---------------------------------------------------------------------------

def _progress_work_count(conn: sqlite3.Connection, user_id: int) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT COUNT(*) as c FROM battles WHERE attacker_id = ? AND created_at LIKE ?",
        (user_id, f"{today}%"),
    )
    return cur.fetchone()["c"]


def _progress_gop_count(conn: sqlite3.Connection, user_id: int) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT COUNT(*) as c FROM battles WHERE attacker_id = ? AND winner_id = ? AND created_at LIKE ?",
        (user_id, user_id, f"{today}%"),
    )
    return cur.fetchone()["c"]


def _progress_turnik_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Считаем число турник-тренировок сегодня через battles."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT COUNT(*) as c FROM battles WHERE attacker_id = ? AND winner_id = ? AND is_npc = 1 AND created_at LIKE ?",
        (user_id, user_id, f"{today}%"),
    )
    return cur.fetchone()["c"]


def _progress_bazar_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Считаем базар-активности через gop_stats.times_called."""
    cur = conn.execute(
        "SELECT times_called FROM gop_stats WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    return row["times_called"] if row else 0


def _progress_total_wins(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.execute("SELECT wins FROM users WHERE tg_id = ?", (user_id,))
    row = cur.fetchone()
    return row["wins"] if row else 0


def _progress_rating_200(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.execute("SELECT rating FROM users WHERE tg_id = ?", (user_id,))
    row = cur.fetchone()
    return row["rating"] if row else 0


def _progress_money_5000(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.execute("SELECT money FROM users WHERE tg_id = ?", (user_id,))
    row = cur.fetchone()
    return row["money"] if row else 0


# Маппинг progress_type → функция
_PROGRESS_FNS = {
    "work_count": _progress_work_count,
    "gop_count": _progress_gop_count,
    "turnik_count": _progress_turnik_count,
    "bazar_count": _progress_bazar_count,
    "total_wins": _progress_total_wins,
    "rating_200": _progress_rating_200,
    "money_5000": _progress_money_5000,
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _progress_key(code: str) -> str:
    """Уникальный ключ для прогресса: code + (date if daily)."""
    if code in ("daily_work", "daily_gop", "daily_turnik", "daily_bazar"):
        return f"{code}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    return code


def seed_quests(conn: sqlite3.Connection) -> int:
    """Вставляет seed-данные квестов. Возвращает кол-во вставленных."""
    count = 0
    for qd in QUEST_DEFINITIONS:
        try:
            conn.execute(
                """INSERT INTO quests (code, title, description, type, xp_reward, money_reward, authority_reward)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    qd["code"],
                    qd["title"],
                    qd["description"],
                    qd["type"],
                    qd["xp_reward"],
                    qd["money_reward"],
                    qd["authority_reward"],
                ),
            )
            count += 1
        except sqlite3.IntegrityError:
            pass  # уже вставлено
    return count


def _get_quest_def(conn: sqlite3.Connection, code: str) -> Optional[QuestInfo]:
    """Получает определение квеста по коду."""
    cur = conn.execute(
        "SELECT * FROM quests WHERE code = ?", (code,)
    )
    row = cur.fetchone()
    if not row:
        return None
    return QuestInfo(
        code=row["code"],
        title=row["title"],
        description=row["description"],
        quest_type=row["type"],
        xp_reward=row["xp_reward"],
        money_reward=row["money_reward"],
        authority_reward=row["authority_reward"],
    )


def get_active_quests(conn: sqlite3.Connection, user_id: int) -> list[QuestProgress]:
    """Получает список активных квестов юзера."""
    # 1. Получаем активные квесты из user_active_quests
    active_codes = set()
    cur = conn.execute(
        "SELECT quest_code FROM user_active_quests WHERE user_id = ? AND completed_at IS NULL",
        (user_id,),
    )
    for row in cur.fetchall():
        active_codes.add(row["quest_code"])

    # 2. Добавляем дaily-квесты, если сегодня не выполнены
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_codes = [
        "daily_work", "daily_gop", "daily_turnik", "daily_bazar",
    ]
    for dc in daily_codes:
        key = f"{dc}:{today_str}"
        # Проверяем, не выполнен ли сегодня
        cur = conn.execute(
            "SELECT COUNT(*) as c FROM user_quests WHERE quest_id = ? AND user_id = ? AND status = 'completed' AND completed_at LIKE ?",
            (dc, user_id, f"{today_str}%"),
        )
        if cur.fetchone()["c"] == 0:
            active_codes.add(dc)

    # 3. Добавляем single-квесты, если не выполнены
    cur = conn.execute(
        "SELECT q.code FROM quests q LEFT JOIN user_quests u ON q.code = CAST(u.quest_id AS TEXT) WHERE q.type = 'single' AND u.user_id IS NULL"
    )
    for row in cur.fetchall():
        active_codes.add(row["code"])

    # 4. Собираем прогресс
    result = []
    for code in active_codes:
        qd = _get_quest_def(conn, code)
        if not qd:
            continue

        # Определяем прогресс-трекер
        progress_fn = None
        target = 1
        for qdef in QUEST_DEFINITIONS:
            if qdef["code"] == code:
                progress_fn = _PROGRESS_FNS.get(qdef.get("progress_type", ""))
                target = qdef.get("target", 1)
                break

        if progress_fn:
            current = progress_fn(conn, user_id)
        else:
            # Без трекера — 0 progress
            current = 0

        completed = current >= target

        result.append(QuestProgress(
            quest=qd,
            current=current,
            target=target,
            completed=completed,
        ))

    return result


def claim_quest(conn: sqlite3.Connection, user_id: int, quest_code: str) -> Optional[QuestReward]:
    """Claim rewards for a completed quest. Returns QuestReward or None."""
    qd = _get_quest_def(conn, quest_code)
    if not qd:
        return None

    progress_fn = None
    target = 1
    for qdef in QUEST_DEFINITIONS:
        if qdef["code"] == quest_code:
            progress_fn = _PROGRESS_FNS.get(qdef.get("progress_type", ""))
            target = qdef.get("target", 1)
            break

    if not progress_fn:
        return None

    current = progress_fn(conn, user_id)
    if current < target:
        return None

    # Проверяем, не получал ли уже награду
    cur = conn.execute(
        "SELECT COUNT(*) as c FROM user_quests WHERE quest_id = ? AND user_id = ? AND status = 'completed'",
        (quest_code, user_id),
    )
    if cur.fetchone()["c"] > 0:
        return None

    # Обновляем user_quests
    conn.execute(
        """INSERT INTO user_quests (quest_id, user_id, status, completed_at)
           VALUES (?, ?, 'completed', datetime('now'))
           ON CONFLICT(quest_id, user_id) DO UPDATE SET status = 'completed', completed_at = datetime('now')""",
        (quest_code, user_id),
    )

    # Обновляем прогресс
    conn.execute(
        """INSERT INTO user_quest_progress (quest_code, user_id, progress, target, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(quest_code, user_id) DO UPDATE SET progress = ?, target = ?, updated_at = datetime('now')""",
        (quest_code, user_id, current, target, current, target),
    )

    # Выдаём награды
    conn.execute(
        "UPDATE users SET money = money + ?, rating = MAX(0, rating - ?), authority = authority + ? WHERE tg_id = ?",
        (qd.money_reward, 0, qd.authority_reward, user_id),
    )

    log.info("User %s completed quest %s (progress %d/%d)", user_id, quest_code, current, target)

    return QuestReward(xp=qd.xp_reward, money=qd.money_reward, authority=qd.authority_reward)
