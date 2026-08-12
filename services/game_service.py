"""GameService — единственный слой игровой логики.

Используется и из PTB-бота, и из FastAPI-веб-аппа.
НЕ ДОЛЖНО быть I/O (сеть, файлы) — только работа с переданным соединением.
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from services.energy_service import check_and_update, add_energy, get_current, _parse_dt, _format_dt
from services.battle_engine import (
    Fighter, simulate, calc_br, calc_hp, calc_initiative_prob,
    BattleResult, Turn, NPC_NAMES,
)
import services.battle_engine as battle_engine
from services.migrate import ensure_schema as _ensure_schema

log = logging.getLogger("gop-bot.game")

_initialized = False


def init(db_path: str | Path) -> None:
    """Инициализация сервиса — вызывается один раз при старте бота/веб-аппа.
    Идемпотентна. Накатывает миграции и открывает глобальное соединение.
    """
    global _initialized
    _ensure_schema(db_path)
    _initialized = True
    log.info("GameService initialized: %s", db_path)


# ---------------------------------------------------------------------------
# Константы из ТЗ
# ---------------------------------------------------------------------------

DISTRICTS = [
    "severniy", "solnechny", "cheremushki", "vzletka",
    "zelenaya_roshcha", "pyatak", "zavodskoy",
]

# Статусы (коды → читаемые названия)
STATUSES = [
    ("lox",         "Лох",          0),
    ("pacan",       "Пацан",        5),
    ("chetkiy",     "Чёткий",       50),
    ("rovny",       "Ровный",       150),
    ("uvazhaemy",   "Уважаемый",    400),
    ("smotryashiy", "Смотрящий",    1000),
    ("legenda",     "Легенда района", 3000),
]

# Стоимости и награды
COSTS = {
    "work":      10,
    "turnik":    8,
    "bazar":     6,
    "mutka":     12,
    "gop":       15,
}

# Нычка: 6 часов кулдаун
NYCHKA_COOLDOWN_HOURS = 6

# PvP: каскадный подбор по диапазонам BR
MATCHMAKING_RANGES = [0.15, 0.25, 0.40]  # ±15%, ±25%, ±40%

# Кланы
CLAN_CREATE_COST = 5000
CLAN_CREATE_STATUS = "chetkiy"  # минимум Чёткий
CLAN_UPGRADE = {
    2: {"cost": 20000, "members": 15},
    3: {"cost": 50000, "members": 20},
}

CLAN_ROLE_BOSS = "boss"
CLAN_ROLE_SMOTRYASHIY = "smotryashiy"
CLAN_ROLE_PATSAN = "patsan"

CLAN_REJOIN_COOLDOWN_HOURS = 24

# Игровые ачивки
ACHIEVEMENTS = {
    "first_gop":         "🥊 Первый гоп",
    "ne_terpila":        "💪 Не терпила",
    "turnikmen":         "🤸 Турникмен",
    "bazar_reshaet":     "🗣 Базар решает",
    "shuhershchik":      "🕵 Шухерщик",
    "glavar":            "👑 Главарь",
    "groza_podezda":     "🚪 Гроза подъезда",
    "semki_magnat":      "🌻 Семочный магнат",
}


ACHIEVEMENT_DESCRIPTIONS = {
    "first_gop":       "Гопни кого-нибудь в первый раз.",
    "ne_terpila":      "Выиграй 3 раза подряд.",
    "turnikmen":       "Сделай 10 тренировок на турниках.",
    "bazar_reshaet":   "Достигни 5 базара.",
    "shuhershchik":    "Уйди от мутки 3 раза.",
    "glavar":          "Достигни 100 рейтинга.",
    "groza_podezda":   "Собери 10 нычек.",
    "semki_magnat":    "Накопи 50 семок.",
}



# ---------------------------------------------------------------------------
# View-объекты (возвращаются хэндлерам)
# ---------------------------------------------------------------------------

@dataclass
class ProfileView:
    tg_id: int
    first_name: str
    username: str
    photo_url: str
    district: str
    district_name: str
    money: int
    semki: int
    energy: int
    energy_max: int
    minutes_to_full: int  # сколько минут до полной энергии
    strength: int
    bazar: int
    stamina: int
    authority: int
    rating: int
    wins: int
    losses: int
    status: str
    status_name: str
    clan_id: Optional[int]
    clan_name: Optional[str]
    br: int = 0
    clan_role: str = "none"  # boss | smotryashiy | patsan | none

    def text(self) -> str:
        # Прогресс до следующего статуса
        from services.game_service import next_status_target, progress_bar
        nxt = next_status_target(self.authority)
        status_block = f"🎖 {self.status_name}"
        if nxt:
            code, name, req, cur = nxt
            bar = progress_bar(cur, req, width=8)
            status_block += f"\n   → {name}: {bar} ({cur}/{req})"
        else:
            status_block += "\n   👑 Топ. Дальше — только стены."

        # Энергия
        if self.energy >= self.energy_max:
            energy_line = f"⚡ {self.energy}/{self.energy_max}"
        else:
            energy_line = f"⚡ {self.energy}/{self.energy_max} (+{self.minutes_to_full} мин)"

        # Клан
        clan_line = f"👥 {self.clan_name}" if self.clan_name else "👥 Без братвы"

        # Заголовок: имя + клан в кавычках
        nick = self.first_name
        return (
            f"👤 {nick}\n"
            f"   «{self.clan_name or 'Одиночка'}»\n"
            f"\n"
            f"📍 {self.district_name}\n"
            f"{status_block}\n"
            f"\n"
            f"💰 {self.money} ₽    🌻 {self.semki} семок\n"
            f"{energy_line}\n"
            f"\n"
            f"📊 Характеристики\n"
            f"  👊 Сила:        {self.strength}\n"
            f"  🗣 Базар:        {self.bazar}\n"
            f"  🛡 Выносливость: {self.stamina}\n"
            f"  ⭐ Авторитет:    {self.authority}\n"
            f"\n"
            f"🏆 Рейтинг: {self.rating}    ⚔️ BR: {self.br}\n"
            f"🥊 {self.wins}W / {self.losses}L\n"
            f"{clan_line}"
        )


@dataclass
class BattleView:
    result: battle_engine.BattleResult
    attacker_id: int
    defender_id: int
    is_npc: bool
    rating_delta: int
    money_delta: int
    energy_lost: int
    attacker_new_rating: int
    attacker_new_money: int
    attacker_new_authority: int


@dataclass
class BattlePreview:
    my_name: str
    my_br: int
    my_strength: int
    my_bazar: int
    my_stamina: int
    my_district: str
    opp_name: str
    opp_br: int
    opp_strength: int
    opp_bazar: int
    opp_stamina: int
    is_npc: bool
    win_chance: float
    energy_cost: int

    def text(self) -> str:
        # Шанс в процентах и визуально
        chance_pct = int(self.win_chance * 100)
        bar_filled = int(self.win_chance * 10)
        chance_bar = "▰" * bar_filled + "▱" * (10 - bar_filled)
        npc_marker = " 🤖 NPC" if self.is_npc else " 👤 Игрок"
        emoji = "🟢" if chance_pct >= 60 else "🟡" if chance_pct >= 40 else "🔴"
        return (
            f"🥊 Гоп!\n\n"
            f"Ты: {self.my_name}\n"
            f"⚔️ BR {self.my_br} · {self.my_strength}💪 {self.my_bazar}🗣 {self.my_stamina}🛡\n"
            f"📍 {self.my_district}\n"
            f"\n"
            f"Противник: {self.opp_name}{npc_marker}\n"
            f"⚔️ BR {self.opp_br} · {self.opp_strength}💪 {self.opp_bazar}🗣 {self.opp_stamina}🛡\n"
            f"\n"
            f"{emoji} Шанс победить: {chance_bar} {chance_pct}%\n"
            f"\n"
            f"Стоимость: {self.energy_cost}⚡\n"
            f"Жми «Гопнуть» чтобы вдарить."
        )

    def summary_text(self) -> str:
        r = self.result
        winner_marker = "🏆 ПОБЕДА" if self.rating_delta >= 0 else "💀 РАЗЪЕБ"
        if r.winner == "attacker":
            return (
                f"{winner_marker}\n\n"
                f"Ты: {r.final_attacker_hp} HP\n"
                f"Противник: {r.final_defender_hp} HP\n"
                f"Ходов: {r.turns_count}\n\n"
                f"💰 +{self.money_delta} ₽\n"
                f"🏆 +{self.rating_delta} рейтинга"
            )
        else:
            return (
                f"{winner_marker}\n\n"
                f"Тебя отпиздили.\n"
                f"Ты: {r.final_attacker_hp} HP\n"
                f"Противник: {r.final_defender_hp} HP\n"
                f"Ходов: {r.turns_count}\n\n"
                f"💸 -{abs(self.money_delta)} ₽\n"
                f"⚡ -{self.energy_lost} энергии\n"
                f"📉 {self.rating_delta} рейтинга"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _status_for_authority(authority: int) -> tuple[str, str]:
    """Возвращает (code, name) для текущего авторитета."""
    code, name = "lox", "Лох"
    for c, n, req in STATUSES:
        if authority >= req:
            code, name = c, n
    return code, name


def _district_name(code: str) -> str:
    if not code:
        return "Не определён"
    cur = sqlite3.connect.__self__ if False else None  # noop
    return code  # по умолчанию показываем код, если lookup не нашли


def get_district_name(conn: sqlite3.Connection, code: str) -> str:
    if not code:
        return "Не определён"
    cur = conn.execute("SELECT name FROM districts WHERE code = ?", (code,))
    row = cur.fetchone()
    return row["name"] if row else code


def next_status_target(authority: int) -> tuple[str, str, int, int] | None:
    """Возвращает (code, name, required, current_progress) следующего статуса.
    None если уже легенда.
    """
    for code, name, req in STATUSES:
        if authority < req:
            return (code, name, req, authority)
    return None


def progress_bar(current: int, target: int, width: int = 10) -> str:
    """Unicode прогресс-бар: ▰▰▰▱▱▱"""
    if target <= 0:
        return ""
    pct = max(0.0, min(1.0, current / target))
    filled = int(pct * width)
    return "▰" * filled + "▱" * (width - filled) + f" {int(pct*100)}%"


def get_clan_name(conn: sqlite3.Connection, clan_id: Optional[int]) -> Optional[str]:
    if not clan_id:
        return None
    cur = conn.execute("SELECT name FROM clans WHERE id = ?", (clan_id,))
    row = cur.fetchone()
    return row["name"] if row else None


def ensure_user(
    conn: sqlite3.Connection,
    tg_id: int,
    first_name: str = "",
    username: str = "",
    last_name: str = "",
    photo_url: str = "",
) -> None:
    """Создаёт запись user если нет, инициализирует дефолты.
    Если уже есть — обновляет first_name/username/photo_url/active_at.
    """
    cur = conn.execute("SELECT tg_id FROM users WHERE tg_id = ?", (tg_id,))
    exists = cur.fetchone() is not None
    if not exists:
        district = random.choice(DISTRICTS)
        conn.execute(
            """INSERT INTO users(
                  tg_id, username, first_name, last_name, photo_url, district,
                  last_energy_at, last_active_at
               ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (tg_id, username, first_name, last_name, photo_url, district),
        )
        log.info("New user %s registered, district=%s", tg_id, district)
    else:
        # Обновляем варьирующиеся поля
        conn.execute(
            """UPDATE users SET
                  username = ?,
                  first_name = ?,
                  last_name = ?,
                  photo_url = ?,
                  last_active_at = datetime('now')
               WHERE tg_id = ?""",
            (username, first_name, last_name, photo_url, tg_id),
        )


# ---------------------------------------------------------------------------
# Профиль
# ---------------------------------------------------------------------------

def get_profile(conn: sqlite3.Connection, tg_id: int) -> Optional[ProfileView]:
    cur = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    if not row:
        return None

    energy, energy_max = get_current(conn, tg_id)
    # минут до полной
    if energy >= energy_max:
        minutes_to_full = 0
    else:
        deficit = energy_max - energy
        minutes_to_full = deficit * 5  # 5 мин на единицу

    status_code, status_name = _status_for_authority(row["authority"])
    br = battle_engine.calc_br(
        row["strength"], row["stamina"], row["bazar"], row["authority"]
    )
    # Достаём clan_role
    clan_role = "none"
    if row["clan_id"]:
        cur2 = conn.execute(
            "SELECT role FROM clan_members WHERE clan_id = ? AND user_id = ? AND left_at IS NULL",
            (row["clan_id"], tg_id),
        )
        cr = cur2.fetchone()
        if cr:
            clan_role = cr["role"]

    return ProfileView(
        tg_id=tg_id,
        first_name=row["first_name"] or row["username"] or f"ID{tg_id}",
        username=row["username"],
        photo_url=row["photo_url"],
        district=row["district"],
        district_name=get_district_name(conn, row["district"]),
        money=row["money"],
        semki=row["semki"],
        energy=energy,
        energy_max=energy_max,
        minutes_to_full=minutes_to_full,
        strength=row["strength"],
        bazar=row["bazar"],
        stamina=row["stamina"],
        authority=row["authority"],
        rating=row["rating"],
        wins=row["wins"],
        losses=row["losses"],
        status=status_code,
        status_name=status_name,
        clan_id=row["clan_id"],
        clan_name=get_clan_name(conn, row["clan_id"]),
        clan_role=clan_role,
        br=br,
    )


# ---------------------------------------------------------------------------
# Рейтинг
# ---------------------------------------------------------------------------

@dataclass
class RatingEntry:
    tg_id: int
    first_name: str
    username: str
    photo_url: str
    district: str
    rating: int
    br: int
    wins: int
    losses: int
    status: str
    status_name: str
    is_self: bool = False


def get_rating(
    conn: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    self_id: Optional[int] = None,
) -> list[RatingEntry]:
    cur = conn.execute(
        """SELECT tg_id, first_name, username, photo_url, district,
                  rating, strength, stamina, bazar, authority, wins, losses
           FROM users
           WHERE last_active_at IS NOT NULL
           ORDER BY rating DESC, wins DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    )
    entries: list[RatingEntry] = []
    for row in cur.fetchall():
        br = battle_engine.calc_br(row["strength"], row["stamina"], row["bazar"], row["authority"])
        code, name = _status_for_authority(row["authority"])
        entries.append(RatingEntry(
            tg_id=row["tg_id"],
            first_name=row["first_name"] or row["username"] or f"ID{row['tg_id']}",
            username=row["username"],
            photo_url=row["photo_url"],
            district=row["district"],
            rating=row["rating"],
            br=br,
            wins=row["wins"],
            losses=row["losses"],
            status=code,
            status_name=name,
            is_self=(self_id == row["tg_id"]),
        ))
    return entries


# ---------------------------------------------------------------------------
# Действия (работа, турники, базар, мутка)
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    ok: bool
    message: str
    profile: Optional[ProfileView] = None
    delta_money: int = 0
    delta_authority: int = 0
    delta_strength: int = 0
    delta_bazar: int = 0
    battle_id: Optional[int] = None  # для PvP
    unlocked: list = field(default_factory=list)  # коды разблокированных ачивок
    delta_stamina: int = 0


def _refresh_profile(conn: sqlite3.Connection, tg_id: int) -> ProfileView:
    return get_profile(conn, tg_id)


def do_work(conn: sqlite3.Connection, tg_id: int) -> ActionResult:
    check = check_and_update(conn, tg_id, COSTS["work"])
    if not check.ok:
        return ActionResult(
            ok=False,
            message=f"⚡ Не хватает энергии. Есть {check.current}, нужно {check.required}. "
                    f"Жди ~{check.minutes_to_wait} мин.",
                    unlocked=_reset_achievements())
    payout = random.randint(80, 120)
    conn.execute(
        "UPDATE users SET money = money + ? WHERE tg_id = ?",
        (payout, tg_id),
    )
    flavor = random.choice([
        "разгрузил фуру", "таскал коробки на рынке",
        "помог на шиномонтажке", "подработал у барыги",
    ])
    return ActionResult(
        ok=True,
        message=f"💼 {flavor.capitalize()}\n💰 +{payout} ₽",
        profile=_refresh_profile(conn, tg_id),
        delta_money=payout,
        unlocked=_reset_achievements())


def train_strength(conn: sqlite3.Connection, tg_id: int) -> ActionResult:
    check = check_and_update(conn, tg_id, COSTS["turnik"])
    if not check.ok:
        return ActionResult(
            ok=False,
            message=f"⚡ Не хватает энергии. Есть {check.current}, нужно {check.required}. "
                    f"Жди ~{check.minutes_to_wait} мин.",
                    unlocked=_reset_achievements())
    conn.execute("UPDATE users SET strength = strength + 1 WHERE tg_id = ?", (tg_id,))
    msg = "💪 Подтягивания, выход силой — качаешься.\n👊 +1 Сила"
    # 15% шанс +1 выносливость
    if random.random() < 0.15:
        conn.execute("UPDATE users SET stamina = stamina + 1 WHERE tg_id = ?", (tg_id,))
        msg += "\n🛡 Бонус: +1 Выносливость"
    return ActionResult(
        ok=True, message=msg,
        profile=_refresh_profile(conn, tg_id),
        delta_strength=1,
        unlocked=_reset_achievements())


def train_bazar(conn: sqlite3.Connection, tg_id: int) -> ActionResult:
    check = check_and_update(conn, tg_id, COSTS["bazar"])
    if not check.ok:
        return ActionResult(
            ok=False,
            message=f"⚡ Не хватает энергии. Есть {check.current}, нужно {check.required}. "
                    f"Жди ~{check.minutes_to_wait} мин.",
                    unlocked=_reset_achievements())
    conn.execute("UPDATE users SET bazar = bazar + 1 WHERE tg_id = ?", (tg_id,))
    msg = "🗣 Потёр с пацанами про жизнь.\n🗣 +1 Базар"
    # 20% шанс +1 авторитет
    if random.random() < 0.20:
        conn.execute("UPDATE users SET authority = authority + 1 WHERE tg_id = ?", (tg_id,))
        msg += "\n⭐ Бонус: +1 Авторитет"
        _maybe_promote(conn, tg_id)
    return ActionResult(
        ok=True, message=msg,
        profile=_refresh_profile(conn, tg_id),
        delta_bazar=1,
        unlocked=_reset_achievements())


def do_mutka(conn: sqlite3.Connection, tg_id: int) -> ActionResult:
    check = check_and_update(conn, tg_id, COSTS["mutka"])
    if not check.ok:
        return ActionResult(
            ok=False,
            message=f"⚡ Не хватает энергии. Есть {check.current}, нужно {check.required}. "
                    f"Жди ~{check.minutes_to_wait} мин.",
                    unlocked=_reset_achievements())
    payout = random.randint(50, 200)
    conn.execute("UPDATE users SET money = money + ? WHERE tg_id = ?", (payout, tg_id))
    msg = f"🤝 Мутка на районе: {random.choice(['постоял на шухере', 'помог барыге', 'организовал сходку', 'достал дефицит', 'разрулил спор у подъезда'])}\n💰 +{payout} ₽"
    delta_authority = 0
    if random.random() < 0.25:
        conn.execute("UPDATE users SET authority = authority + 1 WHERE tg_id = ?", (tg_id,))
        delta_authority = 1
        msg += "\n⭐ +1 Авторитет"
        _maybe_promote(conn, tg_id)
    return ActionResult(
        ok=True, message=msg,
        profile=_refresh_profile(conn, tg_id),
        delta_money=payout, delta_authority=delta_authority,
        unlocked=_reset_achievements())


# Стоимости/награды для новых действий
TEA_COST = 750
TEA_ENERGY = 30
TRAINING_AUTHORITY_GAIN = 1
TRAINING_BAZAR_GAIN = 1
TRAINING_COOLDOWN_HOURS = 3  # анти-спам: тренировка 1 раз в 3 часа

# Кулдаун тренировки
TRAINING_COOLDOWN = timedelta(hours=TRAINING_COOLDOWN_HOURS)


def do_training(conn: sqlite3.Connection, tg_id: int) -> ActionResult:
    """Тренировка в районе (без энергии, но с кулдауном 3ч). +1 авторитет, иногда +1 базар."""
    cur = conn.execute(
        "SELECT authority, last_training_at FROM users WHERE tg_id = ?", (tg_id,)
    )
    row = cur.fetchone()
    if row and row["last_training_at"]:
        last = _parse_dt(row["last_training_at"])
        if last and (datetime.now() - last) < TRAINING_COOLDOWN:
            wait = TRAINING_COOLDOWN - (datetime.now() - last)
            mins = int(wait.total_seconds() // 60)
            return ActionResult(
                ok=False,
                message=f"⏱ Тренировка только раз в {TRAINING_COOLDOWN_HOURS}ч. "
                        f"Подожди ещё ~{mins} мин.",
                unlocked=_reset_achievements(),
            )
    conn.execute(
        "UPDATE users SET authority = authority + ?, last_training_at = datetime('now') "
        "WHERE tg_id = ?",
        (TRAINING_AUTHORITY_GAIN, tg_id),
    )
    msg = f"🏋 Тренировка во дворе: {random.choice(['отжимания от бордюра', 'бой с тенью у гаража', 'турник на школьном дворе', 'скакалка у подъезда'])}."
    msg += f"\n⭐ +{TRAINING_AUTHORITY_GAIN} Авторитет"
    if random.random() < 0.30:
        conn.execute(
            "UPDATE users SET bazar = bazar + ? WHERE tg_id = ?",
            (TRAINING_BAZAR_GAIN, tg_id),
        )
        msg += f"\n🗣 Бонус: +{TRAINING_BAZAR_GAIN} Базар (пацаны зауважали)"
    _maybe_promote(conn, tg_id)
    return ActionResult(
        ok=True, message=msg,
        profile=_refresh_profile(conn, tg_id),
        delta_authority=TRAINING_AUTHORITY_GAIN,
        unlocked=_reset_achievements(),
    )


def buy_tea(conn: sqlite3.Connection, tg_id: int) -> ActionResult:
    """Чайная — за 750₽ покупаешь +30⚡ энергии. Без кулдауна, но дорого."""
    cur = conn.execute(
        "SELECT money, energy, energy_max FROM users WHERE tg_id = ?", (tg_id,)
    )
    row = cur.fetchone()
    if not row:
        return ActionResult(ok=False, message="Сначала регистрация.", unlocked=_reset_achievements())
    money = row["money"]
    energy = row["energy"]
    energy_max = row["energy_max"]
    if money < TEA_COST:
        return ActionResult(
            ok=False,
            message=f"💵 Не хватает на чай. Есть {money}₽, нужно {TEA_COST}₽. "
                    f"Сходи на работу или мутку.",
            unlocked=_reset_achievements(),
        )
    new_energy = min(energy_max, energy + TEA_ENERGY)
    actual = new_energy - energy
    conn.execute(
        "UPDATE users SET money = money - ?, energy = ? WHERE tg_id = ?",
        (TEA_COST, new_energy, tg_id),
    )
    return ActionResult(
        ok=True,
        message=f"☕ Зашёл в чайную. Бабуля налила кружку чая с баранками.\n"
                f"⚡ +{actual} Энергия\n💵 -{TEA_COST} ₽",
        profile=_refresh_profile(conn, tg_id),
        delta_money=-TEA_COST,
        delta_energy=actual,
        unlocked=_reset_achievements(),
    )


def _maybe_promote(conn: sqlite3.Connection, tg_id: int) -> None:
    """Проверить переход по статусам и записать в history."""
    cur = conn.execute("SELECT authority, status FROM users WHERE tg_id = ?", (tg_id,))
    _reset_achievements()  # чистим accumulator для do_mutka

    # Загрузим профиль для проверки ачивок
    profile = get_profile(conn, tg_id)

    # Проверки ачивок
    if profile and profile.money >= 1000:
        _unlock_achievement(conn, tg_id, "shuhershchik")

    # Проверки ачивок
    if profile and profile.bazar >= 5:
        _unlock_achievement(conn, tg_id, "bazar_reshaet")

    # Проверки ачивок
    if profile:
        if profile.strength >= 5:
            _unlock_achievement(conn, tg_id, "turnikmen")
        if profile.strength >= 20:
            _unlock_achievement(conn, tg_id, "ne_terpila")
        if profile.authority >= 100:
            _unlock_achievement(conn, tg_id, "glavar")

    # Проверки ачивок
    if profile:
        semki = profile.semki
        money = profile.money
        if semki >= 50:
            _unlock_achievement(conn, tg_id, "semki_magnat")
        if money >= 5000:
            _unlock_achievement(conn, tg_id, "bazar_reshaet")
    _reset_achievements()  # чистим accumulator для train_bazar
    _reset_achievements()  # чистим accumulator для train_strength
    _reset_achievements()  # чистим accumulator для do_work
    row = cur.fetchone()
    if not row:
        return
    new_code, _ = _status_for_authority(row["authority"])
    if new_code != row["status"]:
        conn.execute(
            "UPDATE users SET status = ? WHERE tg_id = ?",
            (new_code, tg_id),
        )
        conn.execute(
            "INSERT INTO status_history(user_id, old_status, new_status) VALUES (?, ?, ?)",
            (tg_id, row["status"], new_code),
        )
        log.info("User %s: %s → %s", tg_id, row["status"], new_code)


# ---------------------------------------------------------------------------
# Нычка
# ---------------------------------------------------------------------------

@dataclass
class NychkaResult:
    ok: bool
    message: str
    minutes_to_wait: int = 0
    profile: Optional[ProfileView] = None


def claim_nychka(conn: sqlite3.Connection, tg_id: int) -> NychkaResult:
    cur = conn.execute("SELECT last_nychka_at FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    if not row:
        return NychkaResult(ok=False, message="Сначала зарегайся через /start")

    if row["last_nychka_at"]:
        last = _parse_dt(row["last_nychka_at"])
        elapsed_h = (_now() - last).total_seconds() / 3600
        if elapsed_h < NYCHKA_COOLDOWN_HOURS:
            wait_min = int((NYCHKA_COOLDOWN_HOURS - elapsed_h) * 60)
            return NychkaResult(
                ok=False,
                message=f"🕵 Пусто. Тут недавно шмонали. Возвращайся через {wait_min} мин.",
                minutes_to_wait=wait_min,
            )

    # Лут-таблица
    roll = random.random() * 100
    if roll < 55:
        # 50-150₽
        amount = random.randint(50, 150)
        conn.execute("UPDATE users SET money = money + ? WHERE tg_id = ?", (amount, tg_id))
        flavor = random.choice([
            "нашёл заначку за батареей",
            "под ковриком кто-то забыл мелочь",
            "в почтовом ящике лежал подозрительно щедрый подгон",
        ])
        msg = f"🎒 {flavor.capitalize()}.\n💰 +{amount} ₽"
    elif roll < 80:
        amount = random.randint(150, 300)
        conn.execute("UPDATE users SET money = money + ? WHERE tg_id = ?", (amount, tg_id))
        flavor = random.choice([
            "у мусорки валялся забытый пакет с наличкой",
            "один чел оставил конверт на лавке",
        ])
        msg = f"🎒 {flavor.capitalize()}.\n💰 +{amount} ₽"
    elif roll < 95:
        add_energy(conn, tg_id, 5)
        msg = "🎒 Стащил пять единиц энергии из соседского чайника.\n⚡ +5 энергии"
    elif roll < 99:
        conn.execute("UPDATE users SET semki = semki + 1 WHERE tg_id = ?", (tg_id,))
        msg = "🎒 Нашёл одну семку. Удача.\n🌻 +1 семка"
    else:
        conn.execute("UPDATE users SET semki = semki + 3 WHERE tg_id = ?", (tg_id,))
        msg = "🎒 В подъезде валялась пачка — три штуки. Бери, не стесняйся.\n🌻 +3 семки"

    conn.execute(
        "UPDATE users SET last_nychka_at = datetime('now') WHERE tg_id = ?",
        (tg_id,),
    )
    return NychkaResult(ok=True, message=msg, profile=_refresh_profile(conn, tg_id))


# ---------------------------------------------------------------------------
# PvP
# ---------------------------------------------------------------------------

def _make_npc(conn: sqlite3.Connection, target_br: int) -> battle_engine.Fighter:
    """Генерирует NPC c BR близким к target (±20%)."""
    npc_br = max(1, int(target_br * random.uniform(0.85, 1.15)))
    _reset_achievements()  # чистим accumulator для claim_nychka

    # Проверки ачивок — нет таблицы лога нычек, не трогаем пока
    # Раскидываем BR по характеристикам
    strength = max(1, int(npc_br * 0.30))
    stamina = max(1, int(npc_br * 0.20))
    bazar = max(1, int(npc_br * 0.20))
    authority = max(0, int(npc_br * 0.15))
    return battle_engine.Fighter(
        name=random.choice(battle_engine.NPC_NAMES),
        user_id=0,
        strength=strength,
        stamina=stamina,
        bazar=bazar,
        authority=authority,
        is_npc=True,
    )


def find_opponent(conn: sqlite3.Connection, attacker_id: int) -> Optional[battle_engine.Fighter]:
    """Каскадный поиск: ±15% → ±25% → ±40% → None (значит NPC)."""
    cur = conn.execute(
        "SELECT strength, stamina, bazar, authority FROM users WHERE tg_id = ?",
        (attacker_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    my_br = battle_engine.calc_br(
        row["strength"], row["stamina"], row["bazar"], row["authority"]
    )

    week_ago = _now().timestamp() - 7 * 86400
    week_ago_str = datetime.fromtimestamp(week_ago, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for ratio in MATCHMAKING_RANGES:
        low = max(0, int(my_br * (1 - ratio)))
        high = int(my_br * (1 + ratio)) + 1
        cur = conn.execute(
            """SELECT tg_id, first_name, username, strength, stamina, bazar, authority
               FROM users
               WHERE tg_id != ?
                 AND last_active_at >= ?
                 AND (strength*1.4 + stamina*1.2 + bazar*1.0 + authority*0.8) BETWEEN ? AND ?
               ORDER BY ABS((strength*1.4 + stamina*1.2 + bazar*1.0 + authority*0.8) - ?)
               LIMIT 1""",
            (attacker_id, week_ago_str, low, high, my_br),
        )
        opp = cur.fetchone()
        if opp:
            name = opp["first_name"] or opp["username"] or f"ID{opp['tg_id']}"
            return battle_engine.Fighter(
                name=name,
                user_id=opp["tg_id"],
                strength=opp["strength"],
                stamina=opp["stamina"],
                bazar=opp["bazar"],
                authority=opp["authority"],
                is_npc=False,
            )
    return None  # значит нужен NPC


def preview_pvp(conn: sqlite3.Connection, attacker_id: int) -> Optional["BattlePreview"]:
    """Показывает соперника без списания энергии.
    Возвращает BattlePreview или None если не хватает энергии.
    """
    # Проверка энергии (НЕ списываем)
    cur = conn.execute(
        "SELECT energy, energy_max, last_energy_at FROM users WHERE tg_id = ?",
        (attacker_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    from services.energy_service import compute_regen, _parse_dt
    regen = compute_regen(row["energy"], row["energy_max"], _parse_dt(row["last_energy_at"]))
    if regen.new_energy < COSTS["gop"]:
        return None

    # Кто ты
    cur = conn.execute(
        "SELECT first_name, username, strength, stamina, bazar, authority, photo_url, district FROM users WHERE tg_id = ?",
        (attacker_id,),
    )
    arow = cur.fetchone()
    if not arow:
        return None
    my_br = calc_br(arow["strength"], arow["stamina"], arow["bazar"], arow["authority"])

    # Ищем соперника
    defender = find_opponent(conn, attacker_id)
    is_npc = defender is None
    if is_npc:
        defender = _make_npc(conn, my_br)

    # Шанс победы (грубо): normalize BR difference
    br_diff = my_br - defender.br
    win_chance = max(0.05, min(0.95, 0.5 + br_diff * 0.02))

    district_name = get_district_name(conn, arow["district"])

    return BattlePreview(
        my_name=arow["first_name"] or arow["username"] or f"ID{attacker_id}",
        my_br=my_br,
        my_strength=arow["strength"],
        my_bazar=arow["bazar"],
        my_stamina=arow["stamina"],
        my_district=district_name,
        opp_name=defender.name,
        opp_br=defender.br,
        opp_strength=defender.strength,
        opp_bazar=defender.bazar,
        opp_stamina=defender.stamina,
        is_npc=is_npc,
        win_chance=win_chance,
        energy_cost=COSTS["gop"],
    )


def _format_battle_log(result, attacker_name: str, max_lines: int = 8) -> str:
    """Мини-реплей: первые max_lines ударов + итог."""
    lines = []
    for i, turn in enumerate(result.turns[:max_lines]):
        who = "👊 Ты" if turn.attacker_name == attacker_name else f"💢 {turn.attacker_name}"
        target = "его" if turn.attacker_name == attacker_name else "тебя"
        lines.append(f"{i+1}. {who} → {target} -{turn.damage} HP")
    if len(result.turns) > max_lines:
        lines.append(f"... ещё {len(result.turns) - max_lines} ударов")
    return "\n".join(lines)


def start_pvp(conn: sqlite3.Connection, attacker_id: int) -> ActionResult:
    """PvP-бой: подбор соперника, расчёт, запись в battles, обновление статов."""
    check = check_and_update(conn, attacker_id, COSTS["gop"])
    _reset_achievements()  # чистим accumulator для start_pvp
    if not check.ok:
        return ActionResult(
            ok=False,
            message=f"⚡ Не хватает энергии. Есть {check.current}, нужно {check.required}. "
                    f"Жди ~{check.minutes_to_wait} мин.",
                    unlocked=_reset_achievements())

    # Кто нападает
    cur = conn.execute(
        """SELECT first_name, username, strength, stamina, bazar, authority
           FROM users WHERE tg_id = ?""",
        (attacker_id,),
    )
    arow = cur.fetchone()
    if not arow:
        return ActionResult(ok=False, message="Сначала /start", unlocked=_reset_achievements())
    attacker = battle_engine.Fighter(
        name=arow["first_name"] or arow["username"] or f"ID{attacker_id}",
        user_id=attacker_id,
        strength=arow["strength"],
        stamina=arow["stamina"],
        bazar=arow["bazar"],
        authority=arow["authority"],
    )

    # Подбор
    defender = find_opponent(conn, attacker_id)
    is_npc = defender is None
    if is_npc:
        defender = _make_npc(conn, attacker.br)

    result = battle_engine.simulate(attacker, defender)
    is_winner = result.winner == "attacker"

    # Награды/штрафы
    rating_delta = 0
    money_delta = 0
    authority_delta = 0
    energy_lost = 0

    if is_winner:
        money_delta = random.randint(20, 60)
        authority_delta = random.randint(1, 3)
        rating_delta = 5 + defender.br // 10
        conn.execute(
            """UPDATE users SET
                  wins = wins + 1,
                  rating = rating + ?,
                  money = money + ?,
                  authority = authority + ?
               WHERE tg_id = ?""",
            (rating_delta, money_delta, authority_delta, attacker_id),
        )
        _maybe_promote(conn, attacker_id)
        if not is_npc:
            # У противника списываем рейтинг
            conn.execute(
                "UPDATE users SET losses = losses + 1, rating = MAX(0, rating - ?) WHERE tg_id = ?",
                (max(1, rating_delta // 2), defender.user_id),
            )
    else:
        # Поражение: 5-15% наличных, 10-20 энергии
        cur = conn.execute("SELECT money FROM users WHERE tg_id = ?", (attacker_id,))
        money_row = cur.fetchone()
        cash = money_row["money"] if money_row else 0
        money_lost = int(cash * random.uniform(0.05, 0.15))
        energy_lost = random.randint(10, 20)
        rating_delta = -(2 + defender.br // 20)
        conn.execute(
            """UPDATE users SET
                  losses = losses + 1,
                  rating = MAX(0, rating + ?),
                  money = MAX(0, money - ?)
               WHERE tg_id = ?""",
            (rating_delta, money_lost, attacker_id),
        )
        # Списываем энергию (не меньше 0)
        cur = conn.execute("SELECT energy FROM users WHERE tg_id = ?", (attacker_id,))
        erow = cur.fetchone()
        new_e = max(0, erow["energy"] - energy_lost)
        conn.execute("UPDATE users SET energy = ? WHERE tg_id = ?", (new_e, attacker_id))
        money_delta = -money_lost
        if not is_npc:
            conn.execute(
                "UPDATE users SET wins = wins + 1, rating = rating + ? WHERE tg_id = ?",
                (abs(rating_delta), defender.user_id),
            )

    # Лог в JSON
    log_json = json.dumps(
        [asdict(t) for t in result.turns],
        ensure_ascii=False,
    )
    winner_id = attacker_id if is_winner else (0 if is_npc else defender.user_id)
    cur_battle = conn.execute(
        """INSERT INTO battles(
               attacker_id, defender_id, winner_id,
               attacker_br, defender_br,
               attacker_hp, defender_hp, turns_count,
               log_json, rating_delta, money_stolen, energy_lost, is_npc
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            attacker_id,
            defender.user_id,
            winner_id,
            result.attacker_br,
            result.defender_br,
            result.final_attacker_hp,
            result.final_defender_hp,
            result.turns_count,
            log_json,
            rating_delta,
            money_delta,
            energy_lost,
            1 if is_npc else 0,
        ),
    )

    # Обновить last_active_at атакующего
    conn.execute("UPDATE users SET last_active_at = datetime('now') WHERE tg_id = ?", (attacker_id,))

    # Первый гоп → ачивка
    if is_winner:
        _unlock_achievement(conn, attacker_id, "first_gop")
    # Проверки ачивок
    cur_a = conn.execute("SELECT wins, rating FROM users WHERE tg_id = ?", (attacker_id,))
    arow = cur_a.fetchone()
    if arow:
        if arow["wins"] >= 10:
            _unlock_achievement(conn, attacker_id, "ne_terpila")
        if arow["rating"] >= 100:
            _unlock_achievement(conn, attacker_id, "glavar")

    # Вернуть view
    profile = _refresh_profile(conn, attacker_id)
    view = BattleView(
        result=result,
        attacker_id=attacker_id,
        defender_id=defender.user_id,
        is_npc=is_npc,
        rating_delta=rating_delta,
        money_delta=money_delta,
        energy_lost=energy_lost,
        attacker_new_rating=profile.rating,
        attacker_new_money=profile.money,
        attacker_new_authority=profile.authority,
    )
    # Мини-реплей
    replay = _format_battle_log(result, attacker.name, max_lines=8)
    if is_winner:
        msg = (
            f"🏆 ПОБЕДА над {result.loser_name}!\n"
            f"⏱ {result.turns_count} ударов · твой HP {result.final_attacker_hp} / его {result.final_defender_hp}\n\n"
            f"💰 +{money_delta} ₽  ⭐ +{authority_delta} авторитета  🏆 +{rating_delta} рейтинга\n\n"
            f"📜 Как было:\n{replay}"
        )
    else:
        msg = (
            f"💀 Тебя отпиздил {result.loser_name}.\n"
            f"⏱ {result.turns_count} ударов · твой HP {result.final_attacker_hp} / его {result.final_defender_hp}\n\n"
            f"💸 -{abs(money_delta)} ₽  📉 {rating_delta} рейтинга  ⚡ -{energy_lost} энергии\n\n"
            f"📜 Как было:\n{replay}"
        )
    return ActionResult(ok=True, message=msg, profile=profile, battle_id=cur_battle.lastrowid if cur_battle else None, unlocked=_reset_achievements())


# ---------------------------------------------------------------------------
# Ачивки
# ---------------------------------------------------------------------------

# Thread-local accumulator для разблокированных ачивок в рамках одного действия
import threading
_ach_unlocked_this_call: list = []
_ach_lock = threading.Lock()


def _reset_achievements() -> list:
    """Очистить accumulator, вернуть предыдущее значение."""
    global _ach_unlocked_this_call
    with _ach_lock:
        prev = list(_ach_unlocked_this_call)
        _ach_unlocked_this_call = []
    return prev


def _unlock_achievement(conn: sqlite3.Connection, user_id: int, code: str) -> bool:
    """Возвращает True если ачивка только что разблокирована."""
    if code not in ACHIEVEMENTS:
        return False
    try:
        conn.execute(
            "INSERT INTO game_achievements(achievement_code, user_id) VALUES (?, ?)",
            (code, user_id),
        )
        log.info("Achievement %s unlocked for user %s", code, user_id)
        with _ach_lock:
            _ach_unlocked_this_call.append(code)
        return True
    except sqlite3.IntegrityError:
        return False  # уже была


def get_user_achievements(conn: sqlite3.Connection, user_id: int) -> list[tuple[str, str]]:
    """[(code, name), ...] для разблокированных."""
    cur = conn.execute(
        "SELECT achievement_code, unlocked_at FROM game_achievements WHERE user_id = ? ORDER BY unlocked_at DESC",
        (user_id,),
    )
    out = []
    for row in cur.fetchall():
        code = row["achievement_code"]
        out.append((code, ACHIEVEMENTS.get(code, code)))
    return out


# ---------------------------------------------------------------------------
# Клановая система
# ---------------------------------------------------------------------------

@dataclass
class ClanView:
    id: int
    name: str
    description: str
    owner_name: str
    level: int
    member_count: int
    max_members: int
    treasury: int
    rating: int
    member_role: str  # boss | smotryashiy | patsan | none


@dataclass
class ClanActionResult:
    ok: bool
    message: str
    clan: Optional[ClanView] = None


CLAN_COST = 5000
CLAN_MIN_AUTHORITY = 50  # нужно "Чёткий"


def _get_clan(conn: sqlite3.Connection, clan_id: int) -> Optional[ClanView]:
    cur = conn.execute(
        """SELECT c.id, c.name, c.description, c.owner_id, c.level, c.max_members,
                  c.treasury, c.rating,
                  cm.role,
                  o.first_name as owner_name,
                  (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id AND left_at IS NULL) as member_count
           FROM clans c
           JOIN clan_members cm ON c.id = cm.clan_id AND cm.user_id = ? AND cm.left_at IS NULL
           JOIN users o ON c.owner_id = o.tg_id
           WHERE c.id = ?""",
        (0, clan_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return ClanView(
        id=row["id"], name=row["name"], description=row["description"],
        owner_name=row["owner_name"], level=row["level"],
        member_count=row["member_count"], max_members=row["max_members"],
        treasury=row["treasury"], rating=row["rating"],
        member_role=row["role"],
    )


def get_user_clan(conn: sqlite3.Connection, user_id: int) -> Optional[ClanView]:
    """Получает клан пользователя."""
    cur = conn.execute(
        """SELECT c.id, c.name, c.description, c.owner_id, c.level, c.max_members,
                  c.treasury, c.rating,
                  cm.role,
                  o.first_name as owner_name,
                  (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id AND left_at IS NULL) as member_count
           FROM clans c
           JOIN clan_members cm ON c.id = cm.clan_id AND cm.user_id = ? AND cm.left_at IS NULL
           JOIN users o ON c.owner_id = o.tg_id
           WHERE c.id = (SELECT clan_id FROM users WHERE tg_id = ?)""",
        (user_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return ClanView(
        id=row["id"], name=row["name"], description=row["description"],
        owner_name=row["owner_name"], level=row["level"],
        member_count=row["member_count"], max_members=row["max_members"],
        treasury=row["treasury"], rating=row["rating"],
        member_role=row["role"],
    )


def create_clan(conn: sqlite3.Connection, user_id: int, name: str) -> ClanActionResult:
    """Создать клан (братву)."""
    # Проверка: нет ли уже в клане
    cur = conn.execute("SELECT clan_id FROM users WHERE tg_id = ?", (user_id,))
    row = cur.fetchone()
    if row and row["clan_id"]:
        clan = _get_clan(conn, row["clan_id"])
        return ClanActionResult(ok=False, message=f"Ты уже в клане: {clan.name if clan else '???'}")

    # Проверка авторитета
    cur = conn.execute("SELECT authority, money FROM users WHERE tg_id = ?", (user_id,))
    ur = cur.fetchone()
    if not ur or ur["authority"] < CLAN_MIN_AUTHORITY:
        return ClanActionResult(
            ok=False,
            message=f"📉 Нужно {CLAN_MIN_AUTHORITY} авторитета для создания братвы. У тебя: {ur['authority']}. "
                    f"Гоняй мутки, базар, гопни кого-нибудь.",
        )
    if not ur or ur["money"] < CLAN_COST:
        return ClanActionResult(
            ok=False,
            message=f"💰 Нужно {CLAN_COST}₽. У тебя: {ur['money']}₽. Фарми работу и мутки.",
        )

    # Проверяем уникальность имени
    cur = conn.execute("SELECT id FROM clans WHERE name = ?", (name,))
    if cur.fetchone():
        return ClanActionResult(ok=False, message=f"Братва с таким названием уже есть. Попробуй другое.")

    # Создаём
    conn.execute(
        "INSERT INTO clans(name, description, owner_id) VALUES (?, '', ?)",
        (name, user_id),
    )
    clan_id = conn.lastrowid

    # Записываем в users
    conn.execute("UPDATE users SET clan_id = ? WHERE tg_id = ?", (clan_id, user_id))

    # Добавляем в clan_members как boss
    conn.execute(
        "INSERT INTO clan_members(clan_id, user_id, role) VALUES (?, ?, 'boss')",
        (clan_id, user_id),
    )

    # Списываем деньги
    conn.execute("UPDATE users SET money = money - ? WHERE tg_id = ?", (CLAN_COST, user_id))

    clan = _get_clan(conn, clan_id)
    return ClanActionResult(ok=True, message=f"👥 Братва '{name}' создана! Ты — босс. Общак: 0₽. Вперёд, пацаны!", clan=clan)


def leave_clan(conn: sqlite3.Connection, user_id: int) -> ClanActionResult:
    """Выйти из клана."""
    cur = conn.execute("SELECT clan_id FROM users WHERE tg_id = ?", (user_id,))
    row = cur.fetchone()
    if not row or not row["clan_id"]:
        return ClanActionResult(ok=False, message="Ты не в братве.")

    clan_id = row["clan_id"]
    # Если босс — нельзя выйти, пока не передаст власть
    cur = conn.execute("SELECT role FROM clan_members WHERE clan_id = ? AND user_id = ?", (clan_id, user_id))
    mr = cur.fetchone()
    if mr and mr["role"] == "boss":
        # Передача власти — передать самому себе нельзя, но можно передать smotryashiy или patsan
        return ClanActionResult(
            ok=False,
            message="Ты босс. Передай власть кому-нибудь или разбань братву.",
        )

    # Выход
    conn.execute("UPDATE clan_members SET left_at = datetime('now') WHERE clan_id = ? AND user_id = ?",
                 (clan_id, user_id))
    conn.execute("UPDATE users SET clan_id = NULL WHERE tg_id = ?", (user_id,))
    clan = _get_clan(conn, clan_id)
    return ClanActionResult(ok=True, message=f"Выйти из братвы '{clan.name if clan else '???'}'. Пока, пацаны.", clan=clan)


def join_clan(conn: sqlite3.Connection, user_id: int, clan_name: str) -> ClanActionResult:
    """Вступить в клан."""
    # Уже в клане?
    cur = conn.execute("SELECT clan_id FROM users WHERE tg_id = ?", (user_id,))
    row = cur.fetchone()
    if row and row["clan_id"]:
        clan = _get_clan(conn, row["clan_id"])
        return ClanActionResult(ok=False, message=f"Ты уже в клане: {clan.name if clan else '???'}")

    # Найти клан по имени
    cur = conn.execute(
        "SELECT id, name FROM clans WHERE name = ?",
        (clan_name,),
    )
    cr = cur.fetchone()
    if not cr:
        return ClanActionResult(ok=False, message=f"Братвы '{clan_name}' не существует. Попробуй другое имя.")

    clan_id = cr["id"]

    # Проверка лимита
    cur = conn.execute(
        "SELECT COUNT(*) as c FROM clan_members WHERE clan_id = ? AND left_at IS NULL",
        (clan_id,),
    )
    if cur.fetchone()["c"] >= 10:
        return ClanActionResult(ok=False, message="Братва полна. Максимум 10 пацанов.")

    # Вступление
    conn.execute(
        "INSERT INTO clan_members(clan_id, user_id, role) VALUES (?, ?, 'patsan')",
        (clan_id, user_id),
    )
    conn.execute("UPDATE users SET clan_id = ? WHERE tg_id = ?", (clan_id, user_id))

    clan = _get_clan(conn, clan_id)
    return ClanActionResult(
        ok=True,
        message=f"👥 Ты в братве '{cr['name']}'. Добро пожаловать, пацан.",
        clan=clan,
    )


def get_clan_info(conn: sqlite3.Connection, clan_id: int) -> Optional[ClanView]:
    """Информация о клане."""
    cur = conn.execute(
        """SELECT c.id, c.name, c.description, c.owner_id, c.level, c.max_members,
                  c.treasury, c.rating,
                  o.first_name as owner_name,
                  (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id AND left_at IS NULL) as member_count
           FROM clans c
           JOIN users o ON c.owner_id = o.tg_id
           WHERE c.id = ?""",
        (clan_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return ClanView(
        id=row["id"], name=row["name"], description=row["description"],
        owner_name=row["owner_name"], level=row["level"],
        member_count=row["member_count"], max_members=row["max_members"],
        treasury=row["treasury"], rating=row["rating"],
        member_role="none",
    )


def get_clan_members(conn: sqlite3.Connection, clan_id: int) -> list[dict]:
    """Список участников клана."""
    cur = conn.execute(
        """SELECT u.tg_id, u.first_name, u.username, cm.role, u.strength, u.stamina, u.bazar, u.authority, u.rating
           FROM clan_members cm
           JOIN users u ON cm.user_id = u.tg_id
           WHERE cm.clan_id = ? AND cm.left_at IS NULL
           ORDER BY
               CASE cm.role WHEN 'boss' THEN 0 WHEN 'smotryashiy' THEN 1 ELSE 2 END,
               u.rating DESC""",
        (clan_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_leaderboard(conn: sqlite3.Connection, limit: int = 20) -> list[ClanView]:
    """Топ кланов по рейтингу."""
    cur = conn.execute(
        """SELECT c.id, c.name, c.rating, c.level, c.treasury,
                  c.owner_id, o.first_name as owner_name,
                  (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id AND left_at IS NULL) as member_count
           FROM clans c
           JOIN users o ON c.owner_id = o.tg_id
           ORDER BY c.rating DESC
           LIMIT ?""",
        (limit,),
    )
    out = []
    for row in cur.fetchall():
        out.append(ClanView(
            id=row["id"], name=row["name"], description="",
            owner_name=row["owner_name"], level=row["level"],
            member_count=row["member_count"], max_members=10,
            treasury=row["treasury"], rating=row["rating"],
            member_role="none",
        ))
    return out


def apply_clan_bonus(conn: sqlite3.Connection, user_id: int) -> dict:
    """Применяет бонусы от клана к текущим статам юзера.
    Возвращает {strength_bonus, bazar_bonus, stamina_bonus, rating_bonus}."""
    cur = conn.execute("SELECT clan_id FROM users WHERE tg_id = ?", (user_id,))
    row = cur.fetchone()
    if not row or not row["clan_id"]:
        return {"strength": 0, "bazar": 0, "stamina": 0, "rating": 0}

    clan_id = row["clan_id"]

    # Бонус за размер клана: +1 к каждому стату за каждого члена
    cur = conn.execute(
        "SELECT COUNT(*) as c FROM clan_members WHERE clan_id = ? AND left_at IS NULL",
        (clan_id,),
    )
    member_count = cur.fetchone()["c"]

    # Бонус за рейтинг клана
    cur = conn.execute("SELECT rating FROM clans WHERE id = ?", (clan_id,))
    clan_rating = cur.fetchone()["rating"] if cur.fetchone() else 0

    strength = max(0, member_count // 5)  # +1 сила каждые 5 участников
    bazar = max(0, member_count // 3)     # +1 базар каждые 3 участника
    stamina = max(0, member_count // 4)   # +1 выносливость каждые 4 участника
    rating_bonus = clan_rating // 100     # +1 рейтинг за каждые 100 рейтинга клана

    return {"strength": strength, "bazar": bazar, "stamina": stamina, "rating": rating_bonus}
