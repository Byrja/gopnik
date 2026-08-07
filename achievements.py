"""Achievement engine for Гопник-бот."""

from db import GopDB


ACHIEVEMENTS = [
    {
        "id": "first_gop",
        "icon": "🥊",
        "title": "Первак",
        "desc": "Первый наезд — инициация",
        "check": lambda db, uid, cid: db.get_user_stats(uid, cid)["times_gopped"] >= 1,
    },
    {
        "id": "from_raion",
        "icon": "🏠",
        "title": "С района",
        "desc": "На тебя наезжали 5 раз",
        "check": lambda db, uid, cid: db.get_user_stats(uid, cid)["times_gopped"] >= 5,
    },
    {
        "id": "ne_slomalsya",
        "icon": "🔥",
        "title": "Не сломался",
        "desc": "Дошёл до 3-го уровня эскалации и не сдался",
        "check": lambda db, uid, cid: _check_level(uid, cid, 3, db),
    },
    {
        "id": "do_ultimatum",
        "icon": "💀",
        "title": "До упора",
        "desc": "Дошёл до 5-го уровня — ультиматум",
        "check": lambda db, uid, cid: _check_level(uid, cid, 5, db),
    },
    {
        "id": "uvazhaemy",
        "icon": "👑",
        "title": "Уважаемый",
        "desc": "Получил уважуху — заслужил уважение гопника",
        "check": lambda db, uid, cid: _check_level(uid, cid, 6, db),
    },
    {
        "id": "troll",
        "icon": "🎭",
        "title": "Тролль",
        "desc": "Наезжал на других 10 раз",
        "check": lambda db, uid, cid: db.get_user_stats(uid, cid)["times_called"] >= 10,
    },
    {
        "id": "loh_pedalny",
        "icon": "🚲",
        "title": "Лох педальный",
        "desc": "На тебя наезжали 10 раз и ты не получил уважуху",
        "check": lambda db, uid, cid: (
            db.get_user_stats(uid, cid)["times_gopped"] >= 10
            and db.get_user_stats(uid, cid)["respect_earned"] == 0
        ),
    },
    {
        "id": "avtoritet",
        "icon": "🏆",
        "title": "Авторитет",
        "desc": "Наезжал 50 раз — настоящий гопник",
        "check": lambda db, uid, cid: db.get_user_stats(uid, cid)["times_called"] >= 50,
    },
    {
        "id": "gop_stop",
        "icon": "🎖",
        "title": "Гоп-стоп",
        "desc": "Наезжал 100 раз — король района",
        "check": lambda db, uid, cid: db.get_user_stats(uid, cid)["times_called"] >= 100,
    },
    {
        "id": "sampler",
        "icon": "🎯",
        "title": "Самонаезд",
        "desc": "Вызвал гопника на самого себя",
        "check": lambda db, uid, cid: _check_self_gop(uid, cid, db),
    },
]


def _check_level(user_id: int, chat_id: int, level: int, db: GopDB) -> bool:
    """Check if user ever reached this escalation level."""
    state = db.get_escalation(chat_id, user_id)
    return state is not None and state["level"] >= level


def _check_self_gop(user_id: int, chat_id: int, db: GopDB) -> bool:
    """Check if user ever called gop on themselves."""
    conn = db._get_conn()
    row = conn.execute(
        "SELECT 1 FROM gop_calls WHERE caller_id = ? AND victim_id = ? AND chat_id = ? LIMIT 1",
        (user_id, user_id, chat_id),
    ).fetchone()
    return row is not None


class AchievementEngine:
    def __init__(self, db: GopDB):
        self.db = db

    def check_all(self, user_id: int, chat_id: int) -> list[dict]:
        """Check all achievements for a user. Returns list of newly unlocked ones."""
        newly_unlocked = []
        for ach in ACHIEVEMENTS:
            try:
                if ach["check"](self.db, user_id, chat_id):
                    was_new = self.db.unlock_achievement(ach["id"], user_id, chat_id)
                    if was_new:
                        newly_unlocked.append(ach)
            except Exception:
                # Achievement check shouldn't crash the bot
                pass
        return newly_unlocked

    def get_all(self) -> list[dict]:
        """Return all possible achievements."""
        return ACHIEVEMENTS