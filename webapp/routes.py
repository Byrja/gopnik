"""FastAPI роуты для Пацанский Ход.

Все игровые действия идут через services.game_service.
initData валидируется через webapp.auth.validate_init_data.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from services import game_service
from services import bosses as boss_svc
from webapp.auth import validate_init_data, WebAppUser

log = logging.getLogger("gop-bot.webapp.routes")

router = APIRouter()

# Глобальный путь к БД — инициализируется в lifespan
_DB_PATH: Optional[Path] = None
_BOT_TOKEN: str = ""


def configure(db_path: Path, bot_token: str) -> None:
    """Должна вызываться из lifespan при старте."""
    global _DB_PATH, _BOT_TOKEN
    _DB_PATH = db_path
    _BOT_TOKEN = bot_token
    game_service.init(db_path)


def get_conn() -> sqlite3.Connection:
    """Новое соединение на запрос (sqlite3.Connection НЕ thread-safe)."""
    if _DB_PATH is None:
        raise RuntimeError("webapp routes not configured")
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _user_from_init_data(init_data: str) -> WebAppUser:
    user = validate_init_data(init_data, _BOT_TOKEN)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return user


def _ensure_registered(conn: sqlite3.Connection, user: WebAppUser) -> None:
    game_service.ensure_user(
        conn,
        tg_id=user.id,
        first_name=user.first_name,
        username=user.username,
        last_name=user.last_name,
        photo_url=user.photo_url,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# HTML страницы
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def page_index():
    return HTMLResponse(_load_template("index.html", nav="dvor"))


@router.get("/profile", response_class=HTMLResponse)
def page_profile():
    return HTMLResponse(_load_template("profile.html", nav="profile"))


@router.get("/actions")
def page_actions():
    """Deprecated: вся функциональность теперь на / (Двор). Редиректим."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=301)


@router.get("/quests", response_class=HTMLResponse)
def page_quests():
    return HTMLResponse(_load_template("quests.html", nav="quests"))


@router.get("/clan", response_class=HTMLResponse)
def page_clan():
    return HTMLResponse(_load_template("clan.html", nav="clan"))


@router.get("/achievements", response_class=HTMLResponse)
def page_achievements():
    return HTMLResponse(_load_template("achievements.html", nav="achievements"))


@router.get("/battle", response_class=HTMLResponse)
def page_battle_default():
    return HTMLResponse(_load_template("battle.html", nav="dvor"))


@router.get("/battle/{battle_id}", response_class=HTMLResponse)
def page_battle(battle_id: int):
    return HTMLResponse(_load_template("battle.html", nav="dvor"))


def _load_template(name: str, **context) -> str:
    """Jinja2 рендеринг шаблона."""
    from fastapi.templating import Jinja2Templates
    from starlette.requests import Request
    here = Path(__file__).resolve().parent
    templates_dir = here / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    # Stub-style request (не используется, но Jinja2 требует)
    class _Req:
        pass
    req = _Req()
    # Рендерим без наследования используя Extension
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(['html', 'xml']),
    )
    tmpl = env.get_template(name)
    return tmpl.render(**context)


# ---------------------------------------------------------------------------
# /api/me
# ---------------------------------------------------------------------------

@router.get("/api/me")
def api_me(x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data")):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        profile = game_service.get_profile(conn, user.id)
        conn.commit()
        if not profile:
            raise HTTPException(404, "Profile not found")
        return _profile_to_dict(profile)
    finally:
        conn.close()


def _profile_to_dict(p) -> dict:
    out = {
        "tg_id": p.tg_id,
        "first_name": p.first_name,
        "username": p.username,
        "photo_url": p.photo_url,
        "district_code": p.district,
        "district_name": p.district_name,
        "money": p.money,
        "semki": p.semki,
        "energy": p.energy,
        "energy_max": p.energy_max,
        "minutes_to_full": p.minutes_to_full,
        "strength": p.strength,
        "bazar": p.bazar,
        "stamina": p.stamina,
        "authority": p.authority,
        "rating": p.rating,
        "wins": p.wins,
        "losses": p.losses,
        "status": p.status,
        "status_name": p.status_name,
        "clan_id": p.clan_id,
        "clan_name": p.clan_name,
        "br": p.br,
    }
    # Добавляем clan_role и district_code
    if hasattr(p, 'clan_role'):
        out["clan_role"] = p.clan_role
    return out


# ---------------------------------------------------------------------------
# /api/rating
# ---------------------------------------------------------------------------

@router.get("/api/rating")
def api_rating(
    limit: int = 50,
    offset: int = 0,
    x_tg_init_data: Optional[str] = Header(None, alias="X-Tg-Init-Data"),
):
    """Топ игроков. initData опциональна — для подсветки 'YOU'."""
    self_id: Optional[int] = None
    if x_tg_init_data:
        user = _user_from_init_data(x_tg_init_data)
        self_id = user.id

    conn = get_conn()
    try:
        if x_tg_init_data:
            _ensure_registered(conn, user)
            conn.commit()
        top = game_service.get_rating(conn, limit=min(limit, 100), offset=offset, self_id=self_id)
        return {
            "entries": [
                {
                    "tg_id": e.tg_id,
                    "first_name": e.first_name,
                    "username": e.username,
                    "photo_url": e.photo_url,
                    "district": e.district,
                    "rating": e.rating,
                    "br": e.br,
                    "wins": e.wins,
                    "losses": e.losses,
                    "status": e.status,
                    "status_name": e.status_name,
                    "is_self": e.is_self,
                }
                for e in top
            ],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /api/action
# ---------------------------------------------------------------------------

class ActionRequest(BaseModel):
    action: str  # work | turnik | bazar | mutka | gop | nychka


@router.post("/api/action")
def api_action(
    req: ActionRequest,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        if req.action == "work":
            r = game_service.do_work(conn, user.id)
        elif req.action == "turnik":
            r = game_service.train_strength(conn, user.id)
        elif req.action == "bazar":
            r = game_service.train_bazar(conn, user.id)
        elif req.action == "mutka":
            r = game_service.do_mutka(conn, user.id)
        elif req.action == "gop":
            r = game_service.start_pvp(conn, user.id)
        elif req.action == "nychka":
            r = game_service.claim_nychka(conn, user.id)
        elif req.action == "training":
            r = game_service.do_training(conn, user.id)
        elif req.action == "chaynaya":
            r = game_service.buy_tea(conn, user.id)
        else:
            raise HTTPException(400, f"Unknown action: {req.action}")
        conn.commit()

        response = {"ok": r.ok, "message": r.message}
        if hasattr(r, "profile") and r.profile:
            response["profile"] = _profile_to_dict(r.profile)
        if hasattr(r, "battle_id") and r.battle_id:
            response["battle_id"] = r.battle_id
        if hasattr(r, "unlocked") and r.unlocked:
            response["unlocked"] = r.unlocked
        return response
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/battle/{battle_id} — последний бой (для реплея)
# ---------------------------------------------------------------------------

@router.get("/api/battle/last")
def api_battle_last(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT * FROM battles
               WHERE attacker_id = ? OR defender_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (user.id, user.id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No battles yet")
        d = dict(row)
        try:
            d["log"] = json.loads(d.pop("log_json", "[]"))
        except json.JSONDecodeError:
            d["log"] = []
        return d
    finally:
        conn.close()


@router.get("/api/battle/{battle_id}")
def api_battle_by_id(
    battle_id: int,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Конкретный бой по ID. Доступен только участникам."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM battles WHERE id = ?",
            (battle_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Battle not found")
        if user.id not in (row["attacker_id"], row["defender_id"]):
            raise HTTPException(403, "Not your battle")
        d = dict(row)
        try:
            d["log"] = json.loads(d.pop("log_json", "[]"))
        except json.JSONDecodeError:
            d["log"] = []
        # Добавляем имена
        cur2 = conn.execute("SELECT first_name FROM users WHERE tg_id = ?", (d["attacker_id"],))
        d["attacker_name"] = cur2.fetchone()["first_name"] if cur2.fetchone() else "Лох"
        cur2 = conn.execute("SELECT first_name FROM users WHERE tg_id = ?", (d["defender_id"],))
        d["defender_name"] = cur2.fetchone()["first_name"] if cur2.fetchone() else "Лох"
        return d
    finally:
        conn.close()


@router.get("/api/battles")
def api_battles_history(
    limit: int = 20,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Список последних боёв игрока."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT b.id, b.attacker_id, b.defender_id, b.winner_id, b.created_at,
                      b.rating_delta, b.money_reward,
                      ua.first_name as attacker_name, ud.first_name as defender_name
               FROM battles b
               LEFT JOIN users ua ON b.attacker_id = ua.tg_id
               LEFT JOIN users ud ON b.defender_id = ud.tg_id
               WHERE b.attacker_id = ? OR b.defender_id = ?
               ORDER BY b.created_at DESC
               LIMIT ?""",
            (user.id, user.id, min(limit, 50)),
        )
        battles = []
        for row in cur.fetchall():
            d = dict(row)
            d["is_win"] = d["winner_id"] == user.id
            d["is_attacker"] = d["attacker_id"] == user.id
            battles.append(d)
        return {"battles": battles}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/clan — клановые API
# ---------------------------------------------------------------------------

class ClanCreateRequest(BaseModel):
    name: str


@router.post("/api/clan/create")
def api_clan_create(
    req: ClanCreateRequest,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        r = game_service.create_clan(conn, user.id, req.name)
        conn.commit()
        out = {"ok": r.ok, "message": r.message}
        if r.clan:
            out["clan"] = _clan_to_dict(r.clan)
        return out
    finally:
        conn.close()


@router.post("/api/clan/join")
def api_clan_join(
    clan_name: str,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        r = game_service.join_clan(conn, user.id, clan_name)
        conn.commit()
        out = {"ok": r.ok, "message": r.message}
        if r.clan:
            out["clan"] = _clan_to_dict(r.clan)
        return out
    finally:
        conn.close()


@router.post("/api/clan/leave")
def api_clan_leave(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        r = game_service.leave_clan(conn, user.id)
        conn.commit()
        out = {"ok": r.ok, "message": r.message}
        if r.clan:
            out["clan"] = _clan_to_dict(r.clan)
        return out
    finally:
        conn.close()


@router.get("/api/clan/my")
def api_clan_my(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        clan = game_service.get_user_clan(conn, user.id)
        if not clan:
            return {"clan": None}
        return {"clan": _clan_to_dict(clan)}
    finally:
        conn.close()


@router.get("/api/clan/leaderboard")
def api_clan_leaderboard(
    limit: int = 20,
    x_tg_init_data: Optional[str] = Header(None, alias="X-Tg-Init-Data"),
):
    conn = get_conn()
    try:
        lb = game_service.get_leaderboard(conn, limit=limit)
        return {"clans": [_clan_to_dict(c) for c in lb]}
    finally:
        conn.close()


@router.get("/api/clan/{clan_id}")
def api_clan_info(
    clan_id: int,
    x_tg_init_data: Optional[str] = Header(None, alias="X-Tg-Init-Data"),
):
    conn = get_conn()
    try:
        clan = game_service.get_clan_info(conn, clan_id)
        if not clan:
            raise HTTPException(404, "Clan not found")
        return {"clan": _clan_to_dict(clan)}
    finally:
        conn.close()


@router.get("/api/clan/{clan_id}/members")
def api_clan_members(
    clan_id: int,
    x_tg_init_data: Optional[str] = Header(None, alias="X-Tg-Init-Data"),
):
    conn = get_conn()
    try:
        members = game_service.get_clan_members(conn, clan_id)
        return {"members": members}
    finally:
        conn.close()


@router.get("/api/clan/bonus")
def api_clan_bonus(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Получить текущие бонусы от клана."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        bonuses = game_service.apply_clan_bonus(conn, user.id)
        conn.commit()
        return bonuses
    finally:
        conn.close()


def _clan_to_dict(c) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "owner_name": c.owner_name,
        "level": c.level,
        "member_count": c.member_count,
        "max_members": c.max_members,
        "treasury": c.treasury,
        "rating": c.rating,
        "member_role": c.member_role,
    }


# ---------------------------------------------------------------------------
# /api/quest — квестовые API
# ---------------------------------------------------------------------------

from services import quests as quest_svc
from services.quests import seed_quests as _seed_quests


@router.get("/api/quest/active")
def api_quest_active(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Получить активные квесты."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        _seed_quests(conn)
        active = quest_svc.get_active_quests(conn, user.id)
        conn.commit()
        result = []
        for qp in active:
            result.append({
                "code": qp.quest.code,
                "title": qp.quest.title,
                "description": qp.quest.description,
                "type": qp.quest.quest_type,
                "current": qp.current,
                "target": qp.target,
                "progress_pct": qp.progress_pct,
                "completed": qp.completed,
                "xp_reward": qp.quest.xp_reward,
                "money_reward": qp.quest.money_reward,
                "authority_reward": qp.quest.authority_reward,
            })
        return {"quests": result}
    finally:
        conn.close()


@router.post("/api/quest/claim")
def api_quest_claim(
    quest_code: str,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Claim награду за квест."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        _seed_quests(conn)
        reward = quest_svc.claim_quest(conn, user.id, quest_code)
        conn.commit()
        if not reward:
            return {"ok": False, "message": "Не выполнено или уже получено"}
        return {
            "ok": True,
            "message": "Задание выполнено!",
            "xp": reward.xp,
            "money": reward.money,
            "authority": reward.authority,
        }
    finally:
        conn.close()


@router.get("/api/quest/completed")
def api_quest_completed(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Выполненные квесты (с историей)."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        cur = conn.execute(
            """SELECT q.code, q.title, q.description, q.type, q.xp_reward, q.money_reward, q.authority_reward,
                      uq.completed_at
               FROM user_quests uq
               JOIN quests q ON uq.quest_id = q.code
               WHERE uq.user_id = ? AND uq.status = 'completed'
               ORDER BY uq.completed_at DESC
               LIMIT 50""",
            (user.id,),
        )
        result = []
        for row in cur.fetchall():
            result.append({
                "code": row["code"],
                "title": row["title"],
                "description": row["description"],
                "type": row["type"],
                "xp_reward": row["xp_reward"],
                "money_reward": row["money_reward"],
                "authority_reward": row["authority_reward"],
                "completed_at": row["completed_at"],
            })
        return {"quests": result}
    finally:
        conn.close()



@router.get("/api/quest/locked")
def api_quest_locked(
    x_tg_init_data: Optional[str] = Header(None, alias="X-Tg-Init-Data"),
):
    """Закрытые/будущие квесты — видны в UI, но недоступны."""
    from services.quests import get_locked_quests
    conn = get_conn()
    try:
        locked = get_locked_quests(conn)
        return {"quests": locked}
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# /api/achievements
# ---------------------------------------------------------------------------

@router.get("/api/achievements")
def api_achievements(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Все ачивки — открытые и закрытые."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        all_ach = game_service.ACHIEVEMENTS
        cur = conn.execute(
            "SELECT achievement_code, unlocked_at FROM game_achievements WHERE user_id = ?",
            (user.id,),
        )
        unlocked_map = {row["achievement_code"]: row["unlocked_at"] for row in cur.fetchall()}

        items = []
        desc_map = getattr(game_service, 'ACHIEVEMENT_DESCRIPTIONS', {})
        for code, name in all_ach.items():
            items.append({
                "code": code,
                "name": name,
                "description": desc_map.get(code, ""),
                "unlocked": code in unlocked_map,
                "unlocked_at": unlocked_map.get(code),
            })
        return {"items": items}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/nychka/status — для countdown в webapp
# ---------------------------------------------------------------------------

@router.get("/api/nychka/status")
def api_nychka_status(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Возвращает last_nychka_at и когда можно следующая."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        cur = conn.execute(
            "SELECT last_nychka_at FROM users WHERE tg_id = ?",
            (user.id,),
        )
        row = cur.fetchone()
        last = row["last_nychka_at"] if row else None
        # Коoldown: 6 часов
        cooldown_hours = 6
        next_at = None
        minutes_left = 0
        available = True
        if last:
            from datetime import datetime, timedelta
            try:
                last_dt = datetime.fromisoformat(last)
                next_dt = last_dt + timedelta(hours=cooldown_hours)
                now = datetime.now()
                diff = (next_dt - now).total_seconds()
                if diff > 0:
                    available = False
                    minutes_left = int(diff / 60)
                    next_at = next_dt.isoformat()
            except (ValueError, TypeError):
                pass
        return {
            "last_nychka_at": last,
            "next_nychka_at": next_at,
            "available": available,
            "minutes_left": minutes_left,
            "cooldown_hours": cooldown_hours,
            "reward": {"money": "20-80", "semki": "0-2"},
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/districts — выбор района
# ---------------------------------------------------------------------------

@router.get("/api/districts")
def api_districts(
    x_tg_init_data: Optional[str] = Header(None, alias="X-Tg-Init-Data"),
):
    """Список районов с бонусами."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT code, name, description, bonus_strength, bonus_bazar, bonus_stamina FROM districts ORDER BY name"
        )
        return {
            "districts": [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "description": r["description"],
                    "bonus_strength": r["bonus_strength"],
                    "bonus_bazar": r["bonus_bazar"],
                    "bonus_stamina": r["bonus_stamina"],
                }
                for r in cur.fetchall()
            ]
        }
    finally:
        conn.close()


@router.post("/api/district/select")
def api_district_select(
    district_code: str,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Сменить район."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        # Проверяем, что район существует
        cur = conn.execute("SELECT name FROM districts WHERE code = ?", (district_code,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "District not found")
        # Стоимость переезда: 500₽
        COST = 500
        cur = conn.execute("SELECT money FROM users WHERE tg_id = ?", (user.id,))
        ur = cur.fetchone()
        if not ur or ur["money"] < COST:
            return {"ok": False, "message": f"Нужно {COST}₽ для переезда. У тебя: {ur['money'] if ur else 0}₽"}
        # Списываем и переезжаем
        conn.execute("UPDATE users SET money = money - ?, district = ? WHERE tg_id = ?", (COST, district_code, user.id))
        conn.commit()
        return {"ok": True, "message": f"Переехал в {row['name']}! -500₽"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/bosses — PVE боссы районов
# ---------------------------------------------------------------------------

@router.get("/api/bosses")
def api_bosses_list(
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Список боссов с инфой для текущего игрока."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        conn.commit()
        return {"bosses": boss_svc.list_bosses(conn, user.id)}
    finally:
        conn.close()


@router.get("/api/bosses/{boss_code}")
def api_boss_info(
    boss_code: str,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        conn.commit()
        info = boss_svc.get_boss(conn, user.id, boss_code)
        if not info:
            raise HTTPException(404, "Boss not found")
        return info
    finally:
        conn.close()


class BossFightRequest(BaseModel):
    boss_code: str


@router.post("/api/boss/fight")
def api_boss_fight(
    req: BossFightRequest,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Бой с боссом."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        _ensure_registered(conn, user)
        result = boss_svc.fight_boss(conn, user.id, req.boss_code)
        # Получим обновлённый профиль
        profile = game_service.get_profile(conn, user.id)
        conn.commit()
        out = {
            "ok": result.ok,
            "message": result.message,
            "is_winner": result.is_winner,
            "boss_code": result.boss_code,
            "boss_name": result.boss_name,
            "log": result.log,
            "money_delta": result.money_delta,
            "xp_delta": result.xp_delta,
            "semki_delta": result.semki_delta,
            "rating_delta": result.rating_delta,
            "boss_hp_remaining": result.boss_hp_remaining,
            "player_hp_remaining": result.player_hp_remaining,
        }
        if profile:
            out["profile"] = _profile_to_dict(profile)
        if result.battle_id:
            out["fight_id"] = result.battle_id
        return out
    finally:
        conn.close()


@router.get("/api/boss/fight/{fight_id}")
def api_boss_fight_replay(
    fight_id: int,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    """Просмотр прошедшего боя с боссом."""
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        d = boss_svc.get_fight(conn, user.id, fight_id)
        if not d:
            raise HTTPException(404, "Fight not found")
        return d
    finally:
        conn.close()


@router.get("/api/boss/fights")
def api_boss_fights_history(
    limit: int = 20,
    x_tg_init_data: str = Header(..., alias="X-Tg-Init-Data"),
):
    user = _user_from_init_data(x_tg_init_data)
    conn = get_conn()
    try:
        return {"fights": boss_svc.list_user_fights(conn, user.id, limit=limit)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTML: страница боссов
# ---------------------------------------------------------------------------

@router.get("/bosses", response_class=HTMLResponse)
def page_bosses():
    return HTMLResponse(_load_template("bosses.html"))
