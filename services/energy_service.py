"""Энергия — ленивая регенерация. БЕЗ фоновых задач.

При каждом действии:
1. Получить (last_energy_at, energy, energy_max) из users
2. Вычислить delta = floor((now - last_energy_at) / 5 мин)
3. new_energy = min(energy_max, energy + delta)
4. last_energy_at += delta * 300 сек
5. Проверить хватает ли new_energy на требуемое действие
6. Если нет — вернуть (False, нужно_подождать_минут)

Все функции чистые — работают с переданным соединением, не делают I/O.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


# Восстановление: 1 единица за 5 минут
REGEN_SECONDS_PER_UNIT = 300


def _parse_dt(s: str | None) -> datetime:
    """Парсит SQLite datetime строку в aware datetime (UTC)."""
    if not s:
        return datetime.now(timezone.utc)
    # SQLite format: 'YYYY-MM-DD HH:MM:SS' (UTC)
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)
    return dt


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class EnergyUpdate:
    new_energy: int
    new_last_energy_at: datetime
    regenerated: int  # сколько начислили


@dataclass
class EnergyCheck:
    ok: bool
    current: int
    required: int
    minutes_to_wait: int  # если ok=False
    update: EnergyUpdate


def compute_regen(
    energy: int,
    energy_max: int,
    last_energy_at: datetime,
    now: datetime | None = None,
) -> EnergyUpdate:
    """Считает новое состояние энергии на момент now.

    Не пишет в БД — только вычисляет. Вызывающий код решает, применять или нет.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    elapsed_sec = max(0, (now - last_energy_at).total_seconds())
    delta = int(elapsed_sec // REGEN_SECONDS_PER_UNIT)
    new_energy = min(energy_max, energy + delta)
    # Сдвигаем last_energy_at только на ту дельту, что реально начислили
    new_last = last_energy_at
    if delta > 0:
        from datetime import timedelta
        new_last = last_energy_at + timedelta(seconds=delta * REGEN_SECONDS_PER_UNIT)
    return EnergyUpdate(
        new_energy=new_energy,
        new_last_energy_at=new_last,
        regenerated=new_energy - energy,
    )


def check_and_update(
    conn: sqlite3.Connection,
    user_id: int,
    required: int,
) -> EnergyCheck:
    """Проверяет хватает ли энергии. Если хватает — списывает и возвращает ok=True.

    Если не хватает — НЕ списывает, возвращает ok=False + minutes_to_wait.
    """
    cur = conn.execute(
        "SELECT energy, energy_max, last_energy_at FROM users WHERE tg_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return EnergyCheck(
            ok=False, current=0, required=required,
            minutes_to_wait=0,
            update=EnergyUpdate(0, datetime.now(timezone.utc), 0),
        )

    energy, energy_max, last_str = row["energy"], row["energy_max"], row["last_energy_at"]
    last_dt = _parse_dt(last_str)
    update = compute_regen(energy, energy_max, last_dt)

    if update.new_energy < required:
        # Не хватает — не списываем, считаем сколько ждать
        deficit = required - update.new_energy
        # ceil: если надо ещё N единиц, ждать ceil(N*5) минут
        minutes = math.ceil(deficit * 5)
        return EnergyCheck(
            ok=False,
            current=update.new_energy,
            required=required,
            minutes_to_wait=minutes,
            update=update,
        )

    # Хватает — списываем и пишем
    new_energy = update.new_energy - required
    conn.execute(
        "UPDATE users SET energy = ?, last_energy_at = ? WHERE tg_id = ?",
        (new_energy, _format_dt(update.new_last_energy_at), user_id),
    )
    return EnergyCheck(
        ok=True,
        current=new_energy,
        required=required,
        minutes_to_wait=0,
        update=EnergyUpdate(new_energy, update.new_last_energy_at, update.regenerated),
    )


def add_energy(conn: sqlite3.Connection, user_id: int, amount: int) -> int:
    """Добавить энергию (бонус, нычка, ...). Возвращает новое значение."""
    if amount <= 0:
        return 0
    # Сначала регенерируем что накопилось
    cur = conn.execute(
        "SELECT energy, energy_max, last_energy_at FROM users WHERE tg_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return 0
    energy, energy_max, last_str = row["energy"], row["energy_max"], row["last_energy_at"]
    update = compute_regen(energy, energy_max, _parse_dt(last_str))
    new_energy = min(energy_max, update.new_energy + amount)
    conn.execute(
        "UPDATE users SET energy = ?, last_energy_at = ? WHERE tg_id = ?",
        (new_energy, _format_dt(update.new_last_energy_at), user_id),
    )
    return new_energy


def get_current(conn: sqlite3.Connection, user_id: int) -> tuple[int, int]:
    """Возвращает (current, max) с учётом регенерации (без записи в БД)."""
    cur = conn.execute(
        "SELECT energy, energy_max, last_energy_at FROM users WHERE tg_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return 0, 100
    update = compute_regen(row["energy"], row["energy_max"], _parse_dt(row["last_energy_at"]))
    return update.new_energy, row["energy_max"]
