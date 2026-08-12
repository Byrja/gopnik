"""Bot handlers for Пацанский Ход (game mode).

Эти хэндлеры работают ТОЛЬКО в личном чате (PRIVATE) и НЕ трогают
чат-режим /gop, /gop_stop и т.д. — это отдельный слой, существующий в main.py.

Все игровые действия идут через services.game_service.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from services import game_service
from services.energy_service import get_current

log = logging.getLogger("gop-bot.bot.handlers.game")

WEBAPP_URL = "https://gopgame.duckdns.org"


# ---------------------------------------------------------------------------
# DB connection — простой пул на контекст бота
# ---------------------------------------------------------------------------

def get_db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    """Возвращает соединение, создаёт в bot_data если нет."""
    if "db" not in context.bot_data:
        from services.migrate import ensure_schema
        # Используем ту же DB что и существующий gop-bot (../data/gop.db)
        from pathlib import Path
        db_path = Path(__file__).resolve().parent.parent.parent / "data" / "gop.db"
        ensure_schema(db_path)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        context.bot_data["db"] = conn
        # Глобальная инициализация сервиса
        game_service.init(db_path)
    return context.bot_data["db"]


# ---------------------------------------------------------------------------
# Главное меню (по ТЗ: 9 кнопок)
# ---------------------------------------------------------------------------

def build_main_menu(is_private: bool = True) -> InlineKeyboardMarkup:
    """Главное меню игры. is_private=False скрывает кнопку веб-аппа."""
    rows = [
        [InlineKeyboardButton("👤 Мой пацан", callback_data="menu:profile")],
        [InlineKeyboardButton("⚡ Дела", callback_data="menu:actions")],
        [InlineKeyboardButton("🥊 Гопнуть", callback_data="menu:gop")],
        [InlineKeyboardButton("🏆 Рейтинг", callback_data="menu:rating")],
        [InlineKeyboardButton("👥 Братва", callback_data="menu:clan")],
        [InlineKeyboardButton("🎒 Нычка", callback_data="menu:nychka")],
        [InlineKeyboardButton("📜 Задания", callback_data="menu:quests")],
        [InlineKeyboardButton("🎖 Ачивки", callback_data="menu:ach")],
    ]
    if is_private:
        rows.append([InlineKeyboardButton(
            "🌐 Открыть веб-апп",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )])
    return InlineKeyboardMarkup(rows)


WELCOME_TEXT = (
    "🚬 Колян-бот — Пацанский Ход\n"
    "\n"
    "Ты зашёл в район. Тут всё серьёзно: братва, базар, рейтинг.\n"
    "Сейчас ты лох, но через пару часов — пацан. А там и до уважаемого недалеко.\n"
    "\n"
    "⚡ Энергия копится сама — 1 единица за 5 минут. Не трать впустую.\n"
    "\n"
    "Что дальше: нажми 👤 Мой пацан — увидишь свой профиль. "
    "Или сразу ⚡ Дела — там работа, турники, мутки.\n"
    "\n"
    "💡 Кнопка 🌐 Открыть веб-апп внизу — там рейтинг и аватарки гопников."
)


# ---------------------------------------------------------------------------
# /start (расширяем существующий)
# ---------------------------------------------------------------------------

async def cmd_start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start в ЛС — регистрация в игре (если ещё нет) + главное меню."""
    if not update.effective_user or update.effective_chat.type != "private":
        return  # в группах не работаем
    user = update.effective_user
    db = get_db(context)
    game_service.ensure_user(
        db,
        tg_id=user.id,
        first_name=user.first_name or "",
        username=user.username or "",
        last_name=user.last_name or "",
        photo_url="",  # photo_url отдельно через getUserProfilePhotos
    )
    db.commit()
    await update.message.reply_text(WELCOME_TEXT, reply_markup=build_main_menu())


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/me — профиль игрока."""
    if not update.effective_user:
        return
    db = get_db(context)
    profile = game_service.get_profile(db, update.effective_user.id)
    if not profile:
        await update.message.reply_text("Сначала /start")
        return
    db.commit()
    await update.message.reply_text(profile.text(), reply_markup=build_main_menu())


# ---------------------------------------------------------------------------
# Действия
# ---------------------------------------------------------------------------

async def cmd_work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    db = get_db(context)
    r = game_service.do_work(db, update.effective_user.id)
    db.commit()
    await update.message.reply_text(r.message, reply_markup=build_main_menu())


async def cmd_turnik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    db = get_db(context)
    r = game_service.train_strength(db, update.effective_user.id)
    db.commit()
    await update.message.reply_text(r.message, reply_markup=build_main_menu())


async def cmd_bazar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    db = get_db(context)
    r = game_service.train_bazar(db, update.effective_user.id)
    db.commit()
    await update.message.reply_text(r.message, reply_markup=build_main_menu())


async def cmd_mutka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    db = get_db(context)
    r = game_service.do_mutka(db, update.effective_user.id)
    db.commit()
    await update.message.reply_text(r.message, reply_markup=build_main_menu())


async def cmd_gop_pvp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/gop_pvp — ручной запуск PvP (отдельно от чатового /gop)."""
    if not update.effective_user:
        return
    db = get_db(context)
    r = game_service.start_pvp(db, update.effective_user.id)
    db.commit()
    await update.message.reply_text(r.message, reply_markup=build_main_menu())


async def cmd_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rating — топ-20 в чате."""
    if not update.effective_user:
        return
    db = get_db(context)
    top = game_service.get_rating(db, limit=20, self_id=update.effective_user.id)
    lines = ["🏆 Топ гопников:\n"]
    for i, e in enumerate(top, 1):
        sm = " ⬅️ ТЫ" if e.is_self else ""
        lines.append(
            f"{i:2}. {e.first_name}{sm}\n"
            f"    ⭐ {e.rating}  ⚔️ BR {e.br}  🥊 {e.wins}W/{e.losses}L  {e.status_name}"
        )
    await update.message.reply_text("\n".join(lines), reply_markup=build_main_menu())


async def cmd_nychka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nychka — забрать нычку."""
    if not update.effective_user:
        return
    db = get_db(context)
    r = game_service.claim_nychka(db, update.effective_user.id)
    db.commit()
    await update.message.reply_text(r.message, reply_markup=build_main_menu())


async def cmd_ach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ach — ачивки."""
    if not update.effective_user:
        return
    db = get_db(context)
    ach = game_service.get_user_achievements(db, update.effective_user.id)
    if not ach:
        text = "🎖 Пока пусто. Иди в ⚡ Дела, качайся, гопай — откроются."
    else:
        lines = ["🎖 Твои ачивки:\n"]
        for code, name in ach:
            lines.append(f"  {name}")
        text = "\n".join(lines)
    await update.message.reply_text(text, reply_markup=build_main_menu())


# ---------------------------------------------------------------------------
# Callback-роутер (для кнопок)
# ---------------------------------------------------------------------------

async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все callback_data из главного меню."""
    query = update.callback_query
    await query.answer()
    if not query.from_user:
        return
    data = query.data
    db = get_db(context)
    uid = query.from_user.id

    if data == "menu:profile":
        profile = game_service.get_profile(db, uid)
        if not profile:
            await query.edit_message_text("Сначала /start")
            return
        await query.edit_message_text(profile.text(), reply_markup=build_main_menu())

    elif data == "menu:actions":
        # 2x2 grid
        cur_e, max_e = get_current(db, uid)
        kb_rows = [
            [InlineKeyboardButton(f"💼 Работа · 10⚡ · 80-120₽\n({cur_e}⚡ есть)", callback_data="do:work")],
            [InlineKeyboardButton(f"💪 Турники · 8⚡ · +1 сила\n({cur_e}⚡ есть)", callback_data="do:turnik")],
            [InlineKeyboardButton(f"🗣 Базар · 6⚡ · +1 базар\n({cur_e}⚡ есть)", callback_data="do:bazar")],
            [InlineKeyboardButton(f"🤝 Мутка · 12⚡ · 50-200₽\n({cur_e}⚡ есть)", callback_data="do:mutka")],
        ]
        kb_rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")])
        kb = InlineKeyboardMarkup(kb_rows)
        await query.edit_message_text(
            f"⚡ Энергии: {cur_e}/{max_e}\n\n"
            "Чем больше статы — тем больше платят и тем жёстче ты в бою.\n"
            "Базар качает инициативу, Сила — урон.",
            reply_markup=kb,
        )

    elif data == "menu:gop":
        # ПРЕВЬЮ: показать соперника, спросить подтверждение
        prev = game_service.preview_pvp(db, uid)
        if not prev:
            cur_e, max_e = get_current(db, uid)
            minutes = max(0, (15 - cur_e) * 5)
            await query.edit_message_text(
                f"⚡ Не хватает энергии на гоп.\n"
                f"Есть {cur_e}/{max_e}, нужно 15⚡.\n"
                f"Жди ~{minutes} мин.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ Дела (покачаться)", callback_data="menu:actions")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")],
                ]),
            )
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🥊 Вдарить! (15⚡)", callback_data="gop:confirm")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")],
        ])
        await query.edit_message_text(prev.text(), reply_markup=kb)

    elif data == "gop:confirm":
        r = game_service.start_pvp(db, uid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🥊 Гопнуть ещё", callback_data="menu:gop")],
            [InlineKeyboardButton("🏆 Рейтинг", callback_data="menu:rating")],
            [InlineKeyboardButton("⬅️ В меню", callback_data="menu:home")],
        ])
        await query.edit_message_text(r.message, reply_markup=kb)

    elif data == "menu:rating":
        # Топ-15 + позиция самого юзера если он за пределами
        top = game_service.get_rating(db, limit=15, self_id=uid)
        lines = ["🏆 Топ гопников:\n"]
        for i, e in enumerate(top, 1):
            sm = " ⬅️ ТЫ" if e.is_self else ""
            lines.append(
                f"{i:2}. {e.first_name}{sm}\n"
                f"     ⭐ {e.rating} · ⚔️ BR {e.br} · 🥊 {e.wins}W/{e.losses}L · {e.status_name}"
            )
        # Если не в топе — показать твоё место
        my_in_top = any(e.is_self for e in top)
        if not my_in_top:
            cur = db.execute("SELECT rating FROM users WHERE tg_id = ?", (uid,))
            my_row = cur.fetchone()
            if my_row:
                cur = db.execute("SELECT COUNT(*) AS c FROM users WHERE rating > ?", (my_row["rating"],))
                higher = cur.fetchone()["c"]
                lines.append(f"\n…\n{higher+1:3}. ТЫ · ⭐ {my_row['rating']}")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🥊 Гопнуть", callback_data="menu:gop")],
                [InlineKeyboardButton("⬅️ В меню", callback_data="menu:home")],
            ]),
        )

    elif data == "menu:clan":
        # Проверка статуса и денег
        cur = db.execute("SELECT money, status, authority FROM users WHERE tg_id = ?", (uid,))
        row = cur.fetchone()
        if not row:
            text = "Сначала /start"
        else:
            money, status_code, authority = row["money"], row["status"], row["authority"]
            # Текущий статус по авторитету
            current_status_name = "Лох"
            for c, n, r in game_service.STATUSES:
                if authority >= r:
                    current_status_name = n
            from services.game_service import next_status_target, progress_bar
            nxt = next_status_target(authority)
            status_progress = ""
            if nxt and nxt[0] == "chetkiy":
                status_progress = f"\n  {progress_bar(authority, 50, 8)} ({authority}/50)"
            elif nxt:
                status_progress = f"\n  до Чёткого (50 авторитета) — у тебя {authority}"
            else:
                status_progress = "\n  ✅ статус позволяет"
            text = (
                f"👥 Братва — скоро\n\n"
                f"Создание:\n"
                f"  💰 5000 ₽  (у тебя {money} ₽)\n"
                f"  🎖 Статус: Чёткий (50 авторитета)\n"
                f"  У тебя сейчас: {current_status_name}{status_progress}\n"
                f"\n"
                f"Братва даёт:\n"
                f"  • общак (взносы)\n"
                f"  • общий рейтинг (PvP-бонус)\n"
                f"  • чатовые события (Этап 5)\n"
                f"\n"
                f"Хочешь быстрее — фарми мутки и базар."
            )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Дела (покачаться)", callback_data="menu:actions")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")],
        ]))

    elif data == "menu:nychka":
        # Сначала проверим кулдаун без списания
        cur = db.execute("SELECT last_nychka_at FROM users WHERE tg_id = ?", (uid,))
        row = cur.fetchone()
        from datetime import datetime, timezone
        from services.energy_service import _parse_dt
        from services.game_service import NYCHKA_COOLDOWN_HOURS, progress_bar
        last = _parse_dt(row["last_nychka_at"]) if row and row["last_nychka_at"] else None
        if last:
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if elapsed < NYCHKA_COOLDOWN_HOURS:
                wait_min = int((NYCHKA_COOLDOWN_HOURS - elapsed) * 60)
                pct = elapsed / NYCHKA_COOLDOWN_HOURS
                bar = progress_bar(int(pct * 100), 100, 12)
                await query.edit_message_text(
                    f"🎒 Нычка пустая.\n\n"
                    f"Следующая через {wait_min} мин.\n"
                    f"Прогресс: {bar}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")],
                    ]),
                )
                return
        # Готово — пробуем забрать
        r = game_service.claim_nychka(db, uid)
        await query.edit_message_text(r.message, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")],
        ]))

    elif data == "menu:quests":
        await query.edit_message_text(
            "📜 Задания\n\n"
            "Пока доступна только тренировка (через ⚡ Дела).\n"
            "Полноценные квесты со Шрупом появятся скоро.",
            reply_markup=build_main_menu(),
        )

    elif data == "menu:ach":
        ach = game_service.get_user_achievements(db, uid)
        if not ach:
            text = "🎖 Пока пусто. Иди в ⚡ Дела, качайся, гопай — откроются."
        else:
            lines = ["🎖 Твои ачивки:\n"]
            for code, name in ach:
                lines.append(f"  {name}")
            text = "\n".join(lines)
        await query.edit_message_text(text, reply_markup=build_main_menu())

    elif data == "menu:home":
        await query.edit_message_text(WELCOME_TEXT, reply_markup=build_main_menu())

    # Команды из подменю "Дела"

    elif data == "menu:clan_detail":
        # Детали конкретного клана
        clan_id = int(query.data.split(":")[1])
        clan = game_service.get_clan_info(db, clan_id)
        if not clan:
            await query.edit_message_text("Братва не найдена.", reply_markup=build_main_menu())
            return
        members = game_service.get_clan_members(db, clan_id)
        member_lines = []
        for m in members[:10]:
            role_icon = "👑" if m["role"] == "boss" else ("🫡" if m["role"] == "smotryashiy" else "👤")
            member_lines.append(f"  {role_icon} {m['first_name']} (⭐{m['rating']}, BR {game_service.calc_br(m['strength'], m['stamina'], m['bazar'], m['authority'])})")
        text = (
            f"👥 {clan.name}\n"
            f"Босс: {clan.owner_name}\n"
            f"Участников: {clan.member_count}/{clan.max_members}\n"
            f"Рейтинг: ⭐{clan.rating} | Уровень: {clan.level}\n"
            f"Общак: 💰{clan.treasury}₽\n\n"
            f"Участники:\n" + ("\n".join(member_lines) if member_lines else "  пока пусто")
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu:clan")],
            ]),
        )

    elif data == "menu:quests_detail":
        # Показать все активные квесты
        from services import quests as quest_svc
        from services.quests import seed_quests
        seed_quests(db)
        active = quest_svc.get_active_quests(db, uid)
        if not active:
            text = "📜 Нет активных заданий. Отдыхай или иди в ⚡ Дела."
        else:
            lines = ["📜 Задания от Шрупа:\n"]
            for qp in active:
                lines.append(f"  {qp.quest.title} ({qp.quest.quest_type})\n")
                lines.append(f"  {qp}\n")
                lines.append(f"  Награда: 💰+{qp.quest.money_reward} 🏆+{qp.quest.xp_reward} ⭐+{qp.quest.authority_reward}\n")
                lines.append("")
            text = "\n".join(lines)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")],
            ]),
        )

    elif data.startswith("quest:claim:"):
        # Claim награду за квест
        quest_code = query.data.split(":")[2]
        from services import quests as quest_svc
        seed_quests(db)
        reward = quest_svc.claim_quest(db, uid, quest_code)
        if reward:
            text = (
                f"🎉 Задание выполнено!\n"
                f"Награда: 💰+{reward.money} 🏆+{reward.xp} ⭐+{reward.authority}\n\n"
                f"Продолжай качаться!"
            )
        else:
            text = "Награда уже получена или задание не выполнено."
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📜 Задания", callback_data="menu:quests_detail")],
                [InlineKeyboardButton("⬅️ В меню", callback_data="menu:home")],
            ]),
        )

    elif data.startswith("clan:join:"):
        # Вступить в клан
        clan_id = query.data.split(":")[1]
        r = game_service.join_clan(db, uid, "")
        # Попробуем по ID
        clan = game_service.get_clan_info(db, int(clan_id))
        if clan:
            r = game_service.join_clan(db, uid, clan.name)
        await query.edit_message_text(r.message, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu:clan")],
        ]))
        db.commit()
        return

    elif data == "clan:create":
        # Запрос имени для создания клана
        await query.edit_message_text(
            "👥 Введи название для своей братвы:\n\n"
            "Примеры: 'Северный Двор', 'Дворовые', 'Черёмушкинская Бригада'\n"
            "Стоимость: 5000₽, нужно 50 авторитета.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Отмена", callback_data="menu:home")],
            ]),
        )

    elif data == "clan:leave":
        r = game_service.leave_clan(db, uid)
        await query.edit_message_text(
            r.message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Братва", callback_data="menu:clan")],
                [InlineKeyboardButton("⬅️ В меню", callback_data="menu:home")],
            ]),
        )

    elif data == "clan:leaderboard":
        lb = game_service.get_leaderboard(db, 15)
        if not lb:
            text = "👥 Пока нет братв. Создай свою!"
        else:
            lines = ["👥 Топ братв:\n"]
            for i, c in enumerate(lb[:15], 1):
                lines.append(f"{i:2}. {c.name} (⭐{c.rating}, {c.member_count}/10)")
            text = "\n".join(lines)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu:clan")],
            ]),
        )

    elif data.startswith("clan:info:"):
        clan_id = int(query.data.split(":")[1])
        clan = game_service.get_clan_info(db, clan_id)
        if not clan:
            await query.edit_message_text("Братва не найдена.", reply_markup=build_main_menu())
            return
        members = game_service.get_clan_members(db, clan_id)
        member_lines = []
        for m in members[:8]:
            role_icon = "👑" if m["role"] == "boss" else ("🫡" if m["role"] == "smotryashiy" else "👤")
            br = game_service.calc_br(m["strength"], m["stamina"], m["bazar"], m["authority"])
            member_lines.append(f"  {role_icon} {m['first_name']} (⭐{m['rating']}, BR {br})")
        text = (
            f"👥 {clan.name}\n"
            f"Босс: {clan.owner_name}\n"
            f"Участников: {clan.member_count}/{clan.max_members}\n"
            f"Рейтинг: ⭐{clan.rating} | Уровень: {clan.level}\n"
            f"Общак: 💰{clan.treasury}₽\n\n"
            f"Участники:\n" + ("\n".join(member_lines) if member_lines else "  пока пусто")
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu:clan")],
            ]),
        )

    elif data == "do:create_clan":
        # Показываем форму создания клана
        cur = db.execute("SELECT authority, money FROM users WHERE tg_id = ?", (uid,))
        ur = cur.fetchone()
        if not ur:
            await query.edit_message_text("Сначала /start")
            return
        authority = ur["authority"]
        money = ur["money"]
        can_create = authority >= CLAN_MIN_AUTHORITY and money >= CLAN_COST
        from services.game_service import CLAN_MIN_AUTHORITY, CLAN_COST
        text = (
            f"👥 Создать братву\n\n"
            f"Стоимость: 💰{CLAN_COST}₽\n"
            f"Требуется: 🎖{CLAN_MIN_AUTHORITY} авторитета\n\n"
            f"У тебя:\n"
            f"  💰 {money}₽\n"
            f"  🎖 {authority} авторитета\n\n"
            f"Братва даёт:\n"
            f"  • +1 к каждому стату за N участников\n"
            f"  • Общий рейтинг клана даёт бонус к рейтингу\n"
            f"  • Общак: взносы участников\n\n"
            f"{'✅ Можешь создавать!' if can_create else '⚠️ Не хватает характеристик.'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Создать", callback_data="clan:create")] if can_create else [],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu:clan")],
        ])
        await query.edit_message_text(text, reply_markup=kb)

    elif data.startswith("do:"):
        action = data.split(":", 1)[1]
        if action == "work":
            r = game_service.do_work(db, uid)
        elif action == "turnik":
            r = game_service.train_strength(db, uid)
        elif action == "bazar":
            r = game_service.train_bazar(db, uid)
        elif action == "mutka":
            r = game_service.do_mutka(db, uid)
        await query.edit_message_text(r.message, reply_markup=build_main_menu())
        db.commit()

    elif data == "do:work":
        r = game_service.do_work(db, uid)
        await query.edit_message_text(r.message, reply_markup=build_main_menu())
    elif data == "do:turnik":
        r = game_service.train_strength(db, uid)
        await query.edit_message_text(r.message, reply_markup=build_main_menu())
    elif data == "do:bazar":
        r = game_service.train_bazar(db, uid)
        await query.edit_message_text(r.message, reply_markup=build_main_menu())
    elif data == "do:mutka":
        r = game_service.do_mutka(db, uid)
        await query.edit_message_text(r.message, reply_markup=build_main_menu())

    db.commit()


# ---------------------------------------------------------------------------
# Регистрация
# ---------------------------------------------------------------------------

def register(app):
    """Регистрирует игровые хэндлеры в приложении PTB."""
    # Команды — только в ЛС
    app.add_handler(CommandHandler("start", cmd_start_game, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("me", cmd_me, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("work", cmd_work, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("turnik", cmd_turnik, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("bazar", cmd_bazar, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("mutka", cmd_mutka, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("nychka", cmd_nychka, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("ach", cmd_ach, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("gop_pvp", cmd_gop_pvp, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("rating", cmd_rating, filters=filters.ChatType.PRIVATE))

    # Callback для меню
    app.add_handler(CallbackQueryHandler(
        handle_menu_callback,
        pattern=r"^(menu:|do:)",
    ))
    log.info("Game handlers registered")
