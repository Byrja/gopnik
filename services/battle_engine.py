"""Боевой движок — чистые функции, без I/O.

Формулы из ТЗ:
    HP = 100 + Stamina * 10
    Damage = Strength * uniform(0.8, 1.2)
    Initiative = 50% + (Bazar_att - Bazar_def) * 2%
    BR = Strength*1.4 + Stamina*1.2 + Bazar*1.0 + Authority*0.8
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal


def calc_hp(stamina: int) -> int:
    return 100 + stamina * 10


def calc_br(strength: int, stamina: int, bazar: int, authority: int) -> int:
    return int(strength * 1.4 + stamina * 1.2 + bazar * 1.0 + authority * 0.8)


def calc_initiative_prob(bazar_att: int, bazar_def: int) -> float:
    """Возвращает вероятность [0..1] что атакующий ходит первым."""
    p = 0.5 + (bazar_att - bazar_def) * 0.02
    return max(0.05, min(0.95, p))  # clamp в разумных пределах


def roll_damage(strength: int) -> int:
    coef = random.uniform(0.8, 1.2)
    raw = strength * coef
    return max(1, int(round(raw)))


def roll_damage_fast(strength: int) -> int:
    """Быстрый урон для активного боя: чуть выше базового чтобы не затягивать."""
    coef = random.uniform(1.0, 1.5)
    raw = strength * coef
    return max(2, int(round(raw)))


@dataclass
class Fighter:
    name: str
    user_id: int
    strength: int
    stamina: int
    bazar: int
    authority: int
    is_npc: bool = False

    @property
    def hp(self) -> int:
        return calc_hp(self.stamina)

    @property
    def br(self) -> int:
        return calc_br(self.strength, self.stamina, self.bazar, self.authority)


@dataclass
class Turn:
    attacker_name: str
    defender_name: str
    damage: int
    attacker_hp_after: int
    defender_hp_after: int
    flavor: str  # атмосферный текст удара


@dataclass
class BattleResult:
    winner: Literal["attacker", "defender"]
    winner_name: str
    loser_name: str
    turns: list[Turn] = field(default_factory=list)
    turns_count: int = 0
    attacker_br: int = 0
    defender_br: int = 0
    final_attacker_hp: int = 0
    final_defender_hp: int = 0


# Фразы ударов для атмосферы
HIT_LINES = [
    "прилетело в челюсть",
    "заехал в скулу",
    "швырнул в грудь",
    "поймал в солнечное",
    "огрел по уху",
    "ударил в бок",
    "засадил под ребро",
    "дал под дых",
    "приложился в висок",
    "съездил по рёбрам",
]

NPC_NAMES = [
    "Колян с Северного", "Толян с Пятака", "Серый с Черёмушек", "Боря с Взлётки",
    "Жека с Заводского", "Саня с Зелёной Рощи", "Миха из Солнечного",
    "Шуруп с Черёмушек", "Кузя из Заводского", "Фитиль с Пятака",
]


def _hit_flavor(attacker: str, defender: str) -> str:
    line = random.choice(HIT_LINES)
    return f"{attacker} {line} → {defender}"


def simulate(attacker: Fighter, defender: Fighter, max_turns: int = 25) -> BattleResult:
    """Прогоняет автобой. Всегда завершается (HP <= 0 или лимит ходов).

    При лимите ходов побеждает тот, у кого больше % HP.
    """
    if attacker.br == 0 and defender.br == 0:
        raise ValueError("Оба бойца с BR=0 — некорректный бой")

    atk_hp = attacker.hp
    def_hp = defender.hp
    init_p = calc_initiative_prob(attacker.bazar, defender.bazar)
    attacker_first = random.random() < init_p

    turns: list[Turn] = []

    for turn_num in range(1, max_turns + 1):
        if attacker_first:
            dmg = roll_damage_fast(attacker.strength)
            def_hp -= dmg
            turns.append(Turn(
                attacker_name=attacker.name,
                defender_name=defender.name,
                damage=dmg,
                attacker_hp_after=atk_hp,
                defender_hp_after=max(0, def_hp),
                flavor=_hit_flavor(attacker.name, defender.name),
            ))
            if def_hp <= 0:
                return BattleResult(
                    winner="attacker",
                    winner_name=attacker.name,
                    loser_name=defender.name,
                    turns=turns,
                    turns_count=turn_num,
                    attacker_br=attacker.br,
                    defender_br=defender.br,
                    final_attacker_hp=atk_hp,
                    final_defender_hp=0,
                )
            # Ответный
            dmg = roll_damage_fast(defender.strength)
            atk_hp -= dmg
            turns.append(Turn(
                attacker_name=defender.name,
                defender_name=attacker.name,
                damage=dmg,
                attacker_hp_after=max(0, atk_hp),
                defender_hp_after=def_hp,
                flavor=_hit_flavor(defender.name, attacker.name),
            ))
            if atk_hp <= 0:
                return BattleResult(
                    winner="defender",
                    winner_name=defender.name,
                    loser_name=attacker.name,
                    turns=turns,
                    turns_count=turn_num,
                    attacker_br=attacker.br,
                    defender_br=defender.br,
                    final_attacker_hp=0,
                    final_defender_hp=def_hp,
                )
        else:
            dmg = roll_damage_fast(defender.strength)
            atk_hp -= dmg
            turns.append(Turn(
                attacker_name=defender.name,
                defender_name=attacker.name,
                damage=dmg,
                attacker_hp_after=max(0, atk_hp),
                defender_hp_after=def_hp,
                flavor=_hit_flavor(defender.name, attacker.name),
            ))
            if atk_hp <= 0:
                return BattleResult(
                    winner="defender",
                    winner_name=defender.name,
                    loser_name=attacker.name,
                    turns=turns,
                    turns_count=turn_num,
                    attacker_br=attacker.br,
                    defender_br=defender.br,
                    final_attacker_hp=0,
                    final_defender_hp=def_hp,
                )
            # Ответный
            dmg = roll_damage_fast(attacker.strength)
            def_hp -= dmg
            turns.append(Turn(
                attacker_name=attacker.name,
                defender_name=defender.name,
                damage=dmg,
                attacker_hp_after=atk_hp,
                defender_hp_after=max(0, def_hp),
                flavor=_hit_flavor(attacker.name, defender.name),
            ))
            if def_hp <= 0:
                return BattleResult(
                    winner="attacker",
                    winner_name=attacker.name,
                    loser_name=defender.name,
                    turns=turns,
                    turns_count=turn_num,
                    attacker_br=attacker.br,
                    defender_br=defender.br,
                    final_attacker_hp=atk_hp,
                    final_defender_hp=0,
                )

    # Лимит ходов — побеждает по % HP
    atk_pct = atk_hp / attacker.hp
    def_pct = def_hp / defender.hp
    if atk_pct >= def_pct:
        return BattleResult(
            winner="attacker",
            winner_name=attacker.name,
            loser_name=defender.name,
            turns=turns,
            turns_count=len(turns),
            attacker_br=attacker.br,
            defender_br=defender.br,
            final_attacker_hp=atk_hp,
            final_defender_hp=def_hp,
        )
    return BattleResult(
        winner="defender",
        winner_name=defender.name,
        loser_name=attacker.name,
        turns=turns,
        turns_count=len(turns),
        attacker_br=attacker.br,
        defender_br=defender.br,
        final_attacker_hp=atk_hp,
        final_defender_hp=def_hp,
    )
