"""PVE — Боссы районов.

Таблицы:
- district_bosses: справочник боссов (по одному на район)
- boss_attempts: попытки игроков + счётчик убийств
- boss_fights: лог боёв с боссами (как battles, но npc_boss_id)

Императивная миграция: создаёт таблицы если их нет, сидит боссов.
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from services.battle_engine import Fighter, simulate, BattleResult

log = logging.getLogger("services.bosses")

# 7 боссов — по одному на район. BR растёт с уровнем игрока, базовые статы средние.
# Награда пропорциональна силе босса.
BOSSES_SEED = [
    {
        "code": "severniy_glava",
        "district_code": "severniy",
        "name": "Лёха Северный",
        "title": "Хозяин Северного",
        "avatar": "/static/bosses/severniy.svg",
        "base_strength": 8,
        "base_stamina": 7,
        "base_bazar": 5,
        "money_reward": 250,
        "xp_reward": 150,
        "semki_reward": 2,
        "flavor": "Суровый, как ветер Северного. Знает каждый закоулок района.",
    },
    {
        "code": "pyatak_barin",
        "district_code": "pyatak",
        "name": "Толян с Пятака",
        "title": "Барыга Пятака",
        "avatar": "/static/bosses/pyatak.svg",
        "base_strength": 6,
        "base_stamina": 6,
        "base_bazar": 9,
        "money_reward": 300,
        "xp_reward": 180,
        "semki_reward": 1,
        "flavor": "Торгует всем на свете. Базар его стихия — но кулак тоже крепкий.",
    },
    {
        "code": "cheremushki_starik",
        "district_code": "cheremushki",
        "name": "Серый из Черёмушек",
        "title": "Старый пацан",
        "avatar": "/static/bosses/cheremushki.svg",
        "base_strength": 7,
        "base_stamina": 9,
        "base_bazar": 6,
        "money_reward": 280,
        "xp_reward": 160,
        "semki_reward": 2,
        "flavor": "Знает каждый подъезд. Может угостить чаем, а может и в ухо дать.",
    },
    {
        "code": "vzletka_malchik",
        "district_code": "vzletka",
        "name": "Боря с Взлётки",
        "title": "Новостройщик",
        "avatar": "/static/bosses/vzletka.svg",
        "base_strength": 5,
        "base_stamina": 5,
        "base_bazar": 7,
        "money_reward": 220,
        "xp_reward": 130,
        "semki_reward": 1,
        "flavor": "Молодой, амбициозный. Носит только новые шмотки, но в драке горячий.",
    },
    {
        "code": "zelenaya_shkola",
        "district_code": "zelenaya_roshcha",
        "name": "Жека из Зелёной Рощи",
        "title": "Гроза школы",
        "avatar": "/static/bosses/zelenaya.svg",
        "base_strength": 6,
        "base_stamina": 8,
        "base_bazar": 7,
        "money_reward": 260,
        "xp_reward": 150,
        "semki_reward": 2,
        "flavor": "Школьные разборки — его конёк. Учителя шарахаются, пацаны уважают.",
    },
    {
        "code": "zavodskoy_master",
        "district_code": "zavodskoy",
        "name": "Саня Заводской",
        "title": "Пролетарий",
        "avatar": "/static/bosses/zavodskoy.svg",
        "base_strength": 9,
        "base_stamina": 10,
        "base_bazar": 4,
        "money_reward": 320,
        "xp_reward": 200,
        "semki_reward": 3,
        "flavor": "Работает на заводе, качается в цеху. За словом в карман не лезет.",
    },
    {
        "code": "solnechny_vedma",
        "district_code": "solnechny",
        "name": "Миха Солнечный",
        "title": "Тихий пацан",
        "avatar": "/static/bosses/solnechny.svg",
        "base_strength": 5,
        "base_stamina": 6,
        "base_bazar": 10,
        "money_reward": 240,
        "xp_reward": 140,
        "semki_reward": 2,
        "flavor": "Тихий, но с языком — на базаре его не перебазарить. Самый опасный во дворе.",
    },
]

ENERGY_COST = 20  # PVE дороже обычного PvP


@dataclass
class BossInfo:
    code: str
    district_code: str
    name: str
    title: str
    avatar: str
    base_strength: int
    base_stamina: int
    base_bazar: int
    money_reward: int
    xp_reward: int
    semki_reward: int
    flavor: str
    # Динамическое (зависят от BR игрока)
    current_strength: int = 0
    current_stamina: int = 0
    current_bazar: int = 0
    current_hp: int = 0
    scaled_money: int = 0
    scaled_xp: int = 0
    player_kills: int = 0
    player_attempts_today: int = 0
    can_attempt: bool = True
    cooldown_minutes: int = 0


@dataclass
class BossFightResult:
    ok: bool
    message: str
    is_winner: bool = False
    boss_code: str = ""
    boss_name: str = ""
    log: list = field(default_factory=list)
    money_delta: int = 0
    xp_delta: int = 0
    semki_delta: int = 0
    rating_delta: int = 0
    battle_id: Optional[int] = None
    boss_hp_remaining: int = 0
    player_hp_remaining: int = 0


# ---------------------------------------------------------------------------
# Миграция
# ---------------------------------------------------------------------------

def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS district_bosses (
        code TEXT PRIMARY KEY,
        district_code TEXT NOT NULL,
        name TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        avatar TEXT NOT NULL DEFAULT '/static/gopnik.png',
        base_strength INTEGER NOT NULL DEFAULT 5,
        base_stamina INTEGER NOT NULL DEFAULT 5,
        base_bazar INTEGER NOT NULL DEFAULT 5,
        money_reward INTEGER NOT NULL DEFAULT 200,
        xp_reward INTEGER NOT NULL DEFAULT 100,
        semki_reward INTEGER NOT NULL DEFAULT 1,
        flavor TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS boss_attempts (
        user_id INTEGER NOT NULL,
        boss_code TEXT NOT NULL,
        attempts_today INTEGER NOT NULL DEFAULT 0,
        attempts_reset_at TEXT,
        last_attempt_at TEXT,
        kills INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, boss_code)
    );

    CREATE TABLE IF NOT EXISTS boss_fights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        boss_code TEXT NOT NULL,
        attacker_id INTEGER NOT NULL,
        winner_id INTEGER NOT NULL,           -- attacker_id or 0 (boss)
        attacker_br INTEGER NOT NULL,
        boss_br INTEGER NOT NULL,
        attacker_hp INTEGER NOT NULL,
        boss_hp INTEGER NOT NULL,
        turns_count INTEGER NOT NULL,
        rating_delta INTEGER NOT NULL DEFAULT 0,
        money_reward INTEGER NOT NULL DEFAULT 0,
        xp_reward INTEGER NOT NULL DEFAULT 0,
        semki_reward INTEGER NOT NULL DEFAULT 0,
        log_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_boss_fights_user ON boss_fights(attacker_id, created_at DESC);
    """)

    # Seed боссов (идемпотентно)
    for b in BOSSES_SEED:
        conn.execute(
            """INSERT OR IGNORE INTO district_bosses
               (code, district_code, name, title, avatar,
                base_strength, base_stamina, base_bazar,
                money_reward, xp_reward, semki_reward, flavor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (b["code"], b["district_code"], b["name"], b["title"], b["avatar"],
             b["base_strength"], b["base_stamina"], b["base_bazar"],
             b["money_reward"], b["xp_reward"], b["semki_reward"], b["flavor"]),
        )


def _ensure_attempts_row(conn: sqlite3.Connection, user_id: int, boss_code: str) -> sqlite3.Row:
    cur = conn.execute(
        "SELECT * FROM boss_attempts WHERE user_id = ? AND boss_code = ?",
        (user_id, boss_code),
    )
    row = cur.fetchone()
    if row:
        return row
    conn.execute(
        "INSERT INTO boss_attempts (user_id, boss_code) VALUES (?, ?)",
        (user_id, boss_code),
    )
    conn.commit()
    cur = conn.execute(
        "SELECT * FROM boss_attempts WHERE user_id = ? AND boss_code = ?",
        (user_id, boss_code),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Список / получение
# ---------------------------------------------------------------------------

def _scale_for_player(boss: sqlite3.Row, player_br: int) -> dict:
    """Считаем масштабированные статы босса под BR игрока.

    Логика: босс должен быть сложнее среднего противника.
    Минимум: base + 0
    Если player_br больше базового BR босса — масштабируем на коэффициент.
    """
    base_br = boss["base_strength"] * 1.4 + boss["base_stamina"] * 1.2 + boss["base_bazar"] * 1.0
    if base_br <= 0:
        scale = 1.0
    else:
        # Босс растёт вместе с игроком, но не так быстро
        scale = max(1.0, (player_br / base_br) ** 0.7) if player_br > base_br else 1.0
    return {
        "strength": max(1, int(boss["base_strength"] * scale)),
        "stamina": max(1, int(boss["base_stamina"] * scale)),
        "bazar": max(1, int(boss["base_bazar"] * scale)),
        "money": int(boss["money_reward"] * scale),
        "xp": int(boss["xp_reward"] * scale),
        "scale": scale,
    }


def list_bosses(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Список всех боссов с инфой для текущего игрока."""
    cur_p = conn.execute(
        "SELECT strength, stamina, bazar, authority FROM users WHERE tg_id = ?",
        (user_id,),
    )
    prow = cur_p.fetchone()
    if not prow:
        return []
    player_br = int(prow["strength"] * 1.4 + prow["stamina"] * 1.2 + prow["bazar"] * 1.0 + prow["authority"] * 0.8)
    boss_rows = conn.execute("SELECT * FROM district_bosses ORDER BY code").fetchall()
    out = []
    now = datetime.now(timezone.utc)
    for b in boss_rows:
        scaled = _scale_for_player(b, player_br)
        # attempts
        arow = _ensure_attempts_row(conn, user_id, b["code"])
        attempts_today = arow["attempts_today"]
        attempts_reset_at = arow["attempts_reset_at"]
        cooldown_minutes = 0
        can_attempt = True
        max_attempts = 3
        if attempts_reset_at and attempts_today >= max_attempts:
            try:
                reset_dt = datetime.fromisoformat(attempts_reset_at)
                if reset_dt > now:
                    can_attempt = False
                    cooldown_minutes = int((reset_dt - now).total_seconds() / 60)
                else:
                    # Сброс — обновим
                    conn.execute(
                        "UPDATE boss_attempts SET attempts_today = 0, attempts_reset_at = NULL WHERE user_id = ? AND boss_code = ?",
                        (user_id, b["code"]),
                    )
                    attempts_today = 0
            except (ValueError, TypeError):
                pass
        boss_hp = 100 + scaled["stamina"] * 10
        out.append({
            "code": b["code"],
            "district_code": b["district_code"],
            "name": b["name"],
            "title": b["title"],
            "avatar": b["avatar"],
            "flavor": b["flavor"],
            "strength": scaled["strength"],
            "stamina": scaled["stamina"],
            "bazar": scaled["bazar"],
            "boss_hp": boss_hp,
            "br": int(scaled["strength"] * 1.4 + scaled["stamina"] * 1.2 + scaled["bazar"] * 1.0),
            "money_reward": scaled["money"],
            "xp_reward": scaled["xp"],
            "semki_reward": b["semki_reward"],
            "player_kills": arow["kills"],
            "attempts_today": attempts_today,
            "attempts_left": max(0, max_attempts - attempts_today),
            "can_attempt": can_attempt,
            "cooldown_minutes": cooldown_minutes,
        })
    return out


def get_boss(conn: sqlite3.Connection, user_id: int, boss_code: str) -> Optional[dict]:
    cur = conn.execute("SELECT * FROM district_bosses WHERE code = ?", (boss_code,))
    b = cur.fetchone()
    if not b:
        return None
    cur_p = conn.execute(
        "SELECT strength, stamina, bazar, authority FROM users WHERE tg_id = ?",
        (user_id,),
    )
    prow = cur_p.fetchone()
    if not prow:
        return None
    player_br = int(prow["strength"] * 1.4 + prow["stamina"] * 1.2 + prow["bazar"] * 1.0 + prow["authority"] * 0.8)
    scaled = _scale_for_player(b, player_br)
    arow = _ensure_attempts_row(conn, user_id, boss_code)
    attempts_today = arow["attempts_today"]
    can_attempt = True
    cooldown_minutes = 0
    if arow["attempts_reset_at"] and attempts_today >= 3:
        try:
            reset_dt = datetime.fromisoformat(arow["attempts_reset_at"])
            if reset_dt > datetime.now(timezone.utc):
                can_attempt = False
                cooldown_minutes = int((reset_dt - datetime.now(timezone.utc)).total_seconds() / 60)
            else:
                conn.execute(
                    "UPDATE boss_attempts SET attempts_today = 0, attempts_reset_at = NULL WHERE user_id = ? AND boss_code = ?",
                    (user_id, boss_code),
                )
                attempts_today = 0
        except (ValueError, TypeError):
            pass
    return {
        "code": b["code"],
        "name": b["name"],
        "title": b["title"],
        "avatar": b["avatar"],
        "flavor": b["flavor"],
        "strength": scaled["strength"],
        "stamina": scaled["stamina"],
        "bazar": scaled["bazar"],
        "boss_hp": 100 + scaled["stamina"] * 10,
        "br": int(scaled["strength"] * 1.4 + scaled["stamina"] * 1.2 + scaled["bazar"] * 1.0),
        "money_reward": scaled["money"],
        "xp_reward": scaled["xp"],
        "semki_reward": b["semki_reward"],
        "player_kills": arow["kills"],
        "attempts_today": attempts_today,
        "attempts_left": max(0, 3 - attempts_today),
        "can_attempt": can_attempt,
        "cooldown_minutes": cooldown_minutes,
    }


# ---------------------------------------------------------------------------
# Бой с боссом
# ---------------------------------------------------------------------------

def fight_boss(conn: sqlite3.Connection, user_id: int, boss_code: str) -> BossFightResult:
    """Бой с боссом. -20⚡, 3 попытки в сутки, запись в boss_fights."""
    cur = conn.execute("SELECT * FROM district_bosses WHERE code = ?", (boss_code,))
    brow = cur.fetchone()
    if not brow:
        return BossFightResult(ok=False, message="Босс не найден")

    # Проверка энергии
    cur = conn.execute("SELECT energy, energy_max, money, first_name, username, strength, stamina, bazar, authority FROM users WHERE tg_id = ?", (user_id,))
    urow = cur.fetchone()
    if not urow:
        return BossFightResult(ok=False, message="Сначала /start")
    if urow["energy"] < ENERGY_COST:
        return BossFightResult(ok=False, message=f"⚡ Нужно {ENERGY_COST}⚡, у тебя {urow['energy']}⚡")

    # Проверка попыток
    arow = _ensure_attempts_row(conn, user_id, boss_code)
    now = datetime.now(timezone.utc)
    attempts_today = arow["attempts_today"]
    reset_at = arow["attempts_reset_at"]
    if reset_at:
        try:
            reset_dt = datetime.fromisoformat(reset_at)
            if reset_dt > now:
                return BossFightResult(
                    ok=False,
                    message=f"🕛 Лимит попыток на сегодня. Возвращайся через {int((reset_dt - now).total_seconds() / 60)} мин.",
                )
            else:
                conn.execute(
                    "UPDATE boss_attempts SET attempts_today = 0, attempts_reset_at = NULL WHERE user_id = ? AND boss_code = ?",
                    (user_id, boss_code),
                )
                attempts_today = 0
        except (ValueError, TypeError):
            pass
    if attempts_today >= 3:
        # Установим reset на следующую полночь UTC
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        conn.execute(
            "UPDATE boss_attempts SET attempts_reset_at = ? WHERE user_id = ? AND boss_code = ?",
            (midnight.isoformat(), user_id, boss_code),
        )
        return BossFightResult(
            ok=False,
            message=f"🕛 Уже 3 попытки за сегодня. Возвращайся в {midnight.strftime('%H:%M')}.",
        )

    # Списываем энергию
    conn.execute("UPDATE users SET energy = energy - ? WHERE tg_id = ?", (ENERGY_COST, user_id))

    # Считаем статы
    player_br = int(urow["strength"] * 1.4 + urow["stamina"] * 1.2 + urow["bazar"] * 1.0 + urow["authority"] * 0.8)
    scaled = _scale_for_player(brow, player_br)

    player = Fighter(
        name=urow["first_name"] or urow["username"] or f"ID{user_id}",
        user_id=user_id,
        strength=urow["strength"],
        stamina=urow["stamina"],
        bazar=urow["bazar"],
        authority=urow["authority"],
        is_npc=False,
    )
    boss_fighter = Fighter(
        name=brow["name"],
        user_id=0,
        strength=scaled["strength"],
        stamina=scaled["stamina"],
        bazar=scaled["bazar"],
        authority=int(scaled["strength"] * 0.3),
        is_npc=True,
    )

    result: BattleResult = simulate(player, boss_fighter)
    is_winner = result.winner == "attacker"

    money_delta = 0
    xp_delta = 0
    semki_delta = 0
    rating_delta = 0
    if is_winner:
        money_delta = scaled["money"]
        xp_delta = scaled["xp"]
        semki_delta = brow["semki_reward"]
        rating_delta = 10 + int(player_br / 20)
        conn.execute(
            """UPDATE users SET
                money = money + ?,
                rating = rating + ?,
                wins = wins + 1
               WHERE tg_id = ?""",
            (money_delta, rating_delta, user_id),
        )
        # Бонус за семки отдельным update
        if semki_delta:
            conn.execute("UPDATE users SET semki = semki + ? WHERE tg_id = ?", (semki_delta, user_id))
        # +1 kill в attempts
        conn.execute(
            "UPDATE boss_attempts SET kills = kills + 1 WHERE user_id = ? AND boss_code = ?",
            (user_id, boss_code),
        )
        msg = (
            f"🏆 ПОБЕДА НАД БОССОМ! {brow['name']} повержен.\n"
            f"💰 +{money_delta}₽ · ⭐ +{rating_delta} рейтинга · 🌻 +{semki_delta} семок\n"
            f"⚡ -{ENERGY_COST}"
        )
    else:
        rating_delta = 0
        # Штраф -1 силы на 1 час через debuff-таблицу (или просто штраф 1-2 единиц)
        loss = max(1, int(urow["money"] * 0.05))
        conn.execute(
            "UPDATE users SET money = MAX(0, money - ?), losses = losses + 1 WHERE tg_id = ?",
            (loss, user_id),
        )
        money_delta = -loss
        msg = (
            f"💀 {brow['name']} уделал тебя.\n"
            f"-{loss}₽ на лечение синяков.\n"
            f"⚡ -{ENERGY_COST}"
        )

    # Инкремент попыток
    new_attempts = attempts_today + 1
    if new_attempts >= 3:
        # При последней попытке ставим reset на завтра
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        conn.execute(
            """UPDATE boss_attempts SET
                attempts_today = ?,
                attempts_reset_at = ?,
                last_attempt_at = datetime('now')
               WHERE user_id = ? AND boss_code = ?""",
            (new_attempts, midnight.isoformat(), user_id, boss_code),
        )
    else:
        conn.execute(
            """UPDATE boss_attempts SET
                attempts_today = ?,
                last_attempt_at = datetime('now')
               WHERE user_id = ? AND boss_code = ?""",
            (new_attempts, user_id, boss_code),
        )

    # Записываем в boss_fights
    log_data = []
    for t in result.turns:
        log_data.append({
            "attacker": t.attacker_name,
            "defender": t.defender_name,
            "damage": t.damage,
            "flavor": t.flavor,
        })
    cur = conn.execute(
        """INSERT INTO boss_fights
           (boss_code, attacker_id, winner_id, attacker_br, boss_br,
            attacker_hp, boss_hp, turns_count, rating_delta, money_reward, xp_reward, semki_reward, log_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            boss_code, user_id, user_id if is_winner else 0,
            player.br, boss_fighter.br,
            result.final_attacker_hp, result.final_defender_hp,
            result.turns_count, rating_delta,
            money_delta if is_winner else 0,
            xp_delta if is_winner else 0,
            semki_delta if is_winner else 0,
            json.dumps(log_data, ensure_ascii=False),
        ),
    )
    battle_id = cur.lastrowid
    conn.commit()

    return BossFightResult(
        ok=True,
        message=msg,
        is_winner=is_winner,
        boss_code=boss_code,
        boss_name=brow["name"],
        log=log_data,
        money_delta=money_delta,
        xp_delta=xp_delta,
        semki_delta=semki_delta,
        rating_delta=rating_delta,
        battle_id=battle_id,
        boss_hp_remaining=result.final_defender_hp,
        player_hp_remaining=result.final_attacker_hp,
    )


def get_fight(conn: sqlite3.Connection, user_id: int, fight_id: int) -> Optional[dict]:
    cur = conn.execute("SELECT * FROM boss_fights WHERE id = ?", (fight_id,))
    row = cur.fetchone()
    if not row:
        return None
    if row["attacker_id"] != user_id:
        return None
    d = dict(row)
    try:
        d["log"] = json.loads(d.pop("log_json", "[]"))
    except json.JSONDecodeError:
        d["log"] = []
    cur_b = conn.execute("SELECT name, avatar, title FROM district_bosses WHERE code = ?", (d["boss_code"],))
    brow = cur_b.fetchone()
    if brow:
        d["boss_name"] = brow["name"]
        d["boss_avatar"] = brow["avatar"]
        d["boss_title"] = brow["title"]
    return d


def list_user_fights(conn: sqlite3.Connection, user_id: int, limit: int = 20) -> list[dict]:
    cur = conn.execute(
        """SELECT bf.id, bf.boss_code, bf.winner_id, bf.created_at,
                  bf.money_reward, bf.xp_reward, bf.semki_reward, bf.rating_delta,
                  bf.attacker_hp, bf.boss_hp, bf.turns_count,
                  db.name as boss_name, db.avatar as boss_avatar
           FROM boss_fights bf
           JOIN district_bosses db ON bf.boss_code = db.code
           WHERE bf.attacker_id = ?
           ORDER BY bf.created_at DESC
           LIMIT ?""",
        (user_id, min(limit, 50)),
    )
    out = []
    for row in cur.fetchall():
        d = dict(row)
        d["is_win"] = d["winner_id"] == user_id
        out.append(d)
    return out
