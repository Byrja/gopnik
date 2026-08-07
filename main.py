#!/usr/bin/env python3
"""Гопник-бот — наезжает, доебывается, уважает достойных."""

import logging
import os
import re
import random
import hashlib
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from db import GopDB
from llm import GopLLM
from achievements import AchievementEngine

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("gop-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
WORMSOFT_API_KEY = os.environ.get("WORMSOFT_API_KEY", "")
WORMSOFT_MODEL = os.environ.get("WORMSOFT_MODEL", "qwen/qwen3:235b-a22b")
WORMSOFT_BASE_URL = os.environ.get("WORMSOFT_BASE_URL", "https://ai.wormsoft.ru/api/gpt/v1")

# ---------------------------------------------------------------------------
# DB + LLM instances
# ---------------------------------------------------------------------------
db = GopDB("data/gop.db")
llm = GopLLM(api_key=WORMSOFT_API_KEY, base_url=WORMSOFT_BASE_URL, model=WORMSOFT_MODEL)
ach_engine = AchievementEngine(db)

# ---------------------------------------------------------------------------
# Menu constants
# ---------------------------------------------------------------------------
MENU_TEXT = (
    "🚬 *Колян-бот — главный меню*\\n\\n"
    "Я Колян. Районный хулиган, наезжаю на всех.\\n\\n"
    "📋 *Что умею:*\\n\\n"
    "🚬 *Наехать* — наезжу на тебя или кого укажешь\\n"
    "📊 *Стата* — посмотри свою статистику\\n"
    "🏅 *Ачивки* — 10 ачивек за наезды\\n"
    "📋 *Кличка* — узнай свою кличку в этом чате\\n\\n"
    "⚙️ *Управление:*\\n"
    "🔄 *Сброс* — обнулить эскалацию\\n"
    "🔇 *Стоп* — отписаться от наездов\\n"
    "❓ *Помощь* — полный список команд\\n\\n"
    "💡 *Совет:* упомяни @kolyan_byrbot в любом чате — "
    "я отвечу от себя (Guest Mode). "
    "Чем чаще наезжаешь — тем жёстче ответы."
)


def build_menu_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Главное меню."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚬 Наехать на себя", callback_data="menu_gop_self"),
        ],
        [
            InlineKeyboardButton("📊 Стата", callback_data="menu_stats"),
            InlineKeyboardButton("🏅 Ачивки", callback_data="menu_achievements"),
        ],
        [
            InlineKeyboardButton("📋 Кличка", callback_data="menu_my_nick"),
        ],
        [
            InlineKeyboardButton("🔄 Сбросить", callback_data="menu_reset"),
            InlineKeyboardButton("🔇 Стоп", callback_data="menu_stop"),
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="menu_help"),
        ],
    ])


def build_gop_keyboard(target_id: int) -> InlineKeyboardMarkup:
    """Кнопки после наезда."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚬 Ещё наехать", callback_data=f"gop_again:{target_id}"),
            InlineKeyboardButton("📊 Стата", callback_data=f"stats_btn:{target_id}"),
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="menu_help"),
            InlineKeyboardButton("← Меню", callback_data="menu_main"),
        ],
    ])


def build_stats_keyboard(chat_id: int, target_id: int) -> InlineKeyboardMarkup:
    """Кнопки на экране статистики."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚬 Наехать", callback_data=f"gop_again:{target_id}"),
            InlineKeyboardButton("🏅 Ачивки", callback_data=f"ach_btn:{target_id}"),
        ],
        [
            InlineKeyboardButton("← Назад", callback_data="menu_main"),
        ],
    ])


def build_achievements_keyboard(target_id: int) -> InlineKeyboardMarkup:
    """Кнопки на экране ачивок."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚬 Наехать", callback_data=f"gop_again:{target_id}"),
        ],
        [
            InlineKeyboardButton("← Назад", callback_data="menu_main"),
        ],
    ])


# ---------------------------------------------------------------------------
# Helper: get or create user
# ---------------------------------------------------------------------------
def ensure_user(user) -> dict:
    """Create user in DB if not exists, return user record."""
    return db.get_or_create_user(
        tg_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )


# ---------------------------------------------------------------------------
# /start — главное меню
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    keyboard = build_menu_keyboard(update.effective_chat.id)
    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(
            MENU_TEXT.format(), parse_mode="Markdown", reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            MENU_TEXT.format(), parse_mode="Markdown", reply_markup=keyboard
        )


# ---------------------------------------------------------------------------
# /gop — main command
# ---------------------------------------------------------------------------
async def cmd_gop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main gop command. /gop or /gop @username"""
    chat_id = update.effective_chat.id
    caller = update.effective_user
    caller_record = ensure_user(caller)

    # Check if caller is blacklisted
    if db.is_blacklisted(caller.id, chat_id):
        await update.message.reply_text("Ты сам отписался от наездов. /gop_resume если передумал.")
        return

    # Determine victim
    victim_record = None
    victim_context = None
    reply_to_message = update.message.reply_to_message

    if context.args and context.args[0].startswith("@"):
        victim_username = context.args[0].lstrip("@")
        victim_record = db.find_user_by_username(victim_username)
        if not victim_record:
            victim_name = victim_username
        else:
            victim_name = victim_record["first_name"] or victim_record["username"] or victim_username
    elif reply_to_message and reply_to_message.from_user:
        victim = reply_to_message.from_user
        victim_record = ensure_user(victim)
        victim_name = victim_record["first_name"] or victim_record["username"] or "лох"
        victim_context = reply_to_message.text or reply_to_message.caption or ""
    else:
        victim_record = caller_record
        victim_name = caller_record["first_name"] or caller_record["username"] or "братан"

    # Check victim blacklist
    if victim_record and db.is_blacklisted(victim_record["tg_id"], chat_id):
        await update.message.reply_text("Этот лох отписался от наездов. Иди наезжай на кого-нибудь другого.")
        return

    # Get escalation state
    target_id = victim_record["tg_id"] if victim_record else 0
    state = db.get_escalation(chat_id, target_id) if target_id else None
    current_level = state["level"] if state else 1

    # Get conversation history
    history = db.get_recent_messages(chat_id, target_id, limit=50) if target_id else []

    # Get or assign nickname
    nickname = None
    if victim_record:
        nickname = db.get_nickname(victim_record["tg_id"], chat_id)

    # Build prompt and call LLM
    response_text = await llm.gop(
        victim_name=victim_name,
        nickname=nickname,
        escalation_level=current_level,
        history=history,
        victim_context=victim_context,
        caller_name=caller_record["first_name"] or caller_record["username"] or "кто-то",
        self_gop=(target_id == caller.id or target_id == 0),
    )

    # Send response with inline keyboard
    keyboard = build_gop_keyboard(target_id)
    if update.callback_query and update.callback_query.message:
        sent = await update.callback_query.edit_message_text(response_text, reply_markup=keyboard)
    else:
        sent = await update.message.reply_text(response_text, reply_markup=keyboard)

    # Save message to history
    db.add_message(
        chat_id=chat_id,
        user_id=target_id if target_id else caller.id,
        role="gop",
        text=response_text,
    )
    if sent and target_id:
        db.save_gop_message(sent.message_id, chat_id, target_id, current_level)

    if target_id:
        db.update_escalation(chat_id, target_id, level=current_level)

    # Check achievements
    new_achievements = ach_engine.check_all(caller.id, chat_id)
    for ach in new_achievements:
        await update.message.reply_text(
            f"🏅 Ачивка разблокирована: {ach['icon']} {ach['title']} — {ach['desc']}"
        )

    if victim_record and victim_record["tg_id"] != caller.id:
        victim_achievements = ach_engine.check_all(victim_record["tg_id"], chat_id)
        for ach in victim_achievements:
            await update.message.reply_text(
                f"🏅 {victim_name} получил ачивку: {ach['icon']} {ach['title']} — {ach['desc']}"
            )


# ---------------------------------------------------------------------------
# Reply handler — for escalation conversations
# ---------------------------------------------------------------------------
async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle replies to gop messages — escalation engine."""
    if not update.message or not update.message.reply_to_message:
        return

    reply_to = update.message.reply_to_message
    if not reply_to.from_user or not reply_to.from_user.is_bot:
        return

    gop_msg = db.get_gop_message(reply_to.message_id, update.effective_chat.id)
    if not gop_msg:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    user_record = ensure_user(user)

    if db.is_blacklisted(user.id, chat_id):
        return

    state = db.get_escalation(chat_id, user.id)
    if not state:
        return

    current_level = state["level"]
    history = db.get_recent_messages(chat_id, user.id, limit=50)
    nickname = db.get_nickname(user.id, chat_id)

    user_text = update.message.text or ""
    response_text = await llm.gop_reply(
        victim_name=user_record["first_name"] or user_record["username"] or "братан",
        nickname=nickname,
        current_level=current_level,
        user_reply=user_text,
        history=history,
    )

    sent = await update.message.reply_text(response_text)
    db.add_message(chat_id=chat_id, user_id=user.id, role="user", text=user_text)
    db.add_message(chat_id=chat_id, user_id=user.id, role="gop", text=response_text)

    new_level = llm.determine_escalation(user_text, current_level)
    db.update_escalation(chat_id, user.id, level=new_level)

    if new_level >= 6:
        db.update_nickname(user.id, chat_id, None)
    elif nickname is None and current_level < 6:
        nicknames = ["Лох", "Чушок", "Ссыкло", "Тормоз", "Шнырь", "Фуфло", "Балабол", "Штрих"]
        db.update_nickname(user.id, chat_id, random.choice(nicknames))

    new_achievements = ach_engine.check_all(user.id, chat_id)
    for ach in new_achievements:
        await update.message.reply_text(
            f"🏅 Ачивка: {ach['icon']} {ach['title']} — {ach['desc']}"
        )


# ---------------------------------------------------------------------------
# /gop_stop — opt out
# ---------------------------------------------------------------------------
async def cmd_gop_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id
    db.add_to_blacklist(user["tg_id"], chat_id)
    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text("🔇 Отписался от наездов. Нажми 'Стоп' в меню, чтобы вернуться.")
    else:
        await update.message.reply_text("🔇 Отписался от наездов. Нажми 'Стоп' в меню, чтобы вернуться.")


# ---------------------------------------------------------------------------
# /gop_resume — opt back in
# ---------------------------------------------------------------------------
async def cmd_gop_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id
    db.remove_from_blacklist(user["tg_id"], chat_id)
    await update.message.reply_text("👋 Возвратился? Держись — теперь я снова наеду.")


# ---------------------------------------------------------------------------
# /gop_stats — statistics (editable)
# ---------------------------------------------------------------------------
async def cmd_gop_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id
    stats = db.get_user_stats(user["tg_id"], chat_id)
    nickname = db.get_nickname(user["tg_id"], chat_id)
    state = db.get_escalation(chat_id, user["tg_id"])

    nickname_str = f"\n📋 Кличка: {nickname}" if nickname else ""
    level_str = f"\n📈 Уровень: {state['level']}/5" if state else "\n📈 Уровень: не начал"

    text = (
        f"📊 *Статистика*\n\n"
        f"🚬 Наезжал: {stats['times_called']} раз\n"
        f"💀 На тебя наезжали: {stats['times_gopped']} раз\n"
        f"🏆 Уважуха: {stats['respect_earned']} раз{nickname_str}{level_str}"
    )
    keyboard = build_stats_keyboard(chat_id, user["tg_id"])

    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# /gop_reset — reset escalation
# ---------------------------------------------------------------------------
async def cmd_gop_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    db.reset_escalation(chat_id, user.id)
    db.update_nickname(user.id, chat_id, None)

    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(
            "🔄 Эскалация и кличка сброшены. Начинаем с чистого листа.\n\n"
            "Нажми '← Меню' чтобы вернуться."
        )
    else:
        await update.message.reply_text(
            "🔄 Эскалация и кличка сброшены. Начинаем с чистого листа."
        )


# ---------------------------------------------------------------------------
# /gop_my_nick — show current nickname
# ---------------------------------------------------------------------------
async def cmd_gop_my_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    nickname = db.get_nickname(user.id, chat_id)
    state = db.get_escalation(chat_id, user.id)
    level_str = f"\n📈 Уровень: {state['level']}/5" if state else ""

    if nickname:
        text = f"📋 Твоя кличка: *{nickname}*\n{level_str}\n\nСбросить: /gop_reset"
    else:
        text = "📋 У тебя пока нет клички. Наезди хоть раз — получишь."

    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /gop_help — full commands list
# ---------------------------------------------------------------------------
async def cmd_gop_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🚬 *Колян-бот — команды*\\n\\n"
        "*Наезды:*\\n"
        "🚬 /gop — наеду на тебя\\n"
        "🚬 /gop @username — наеду на юзера\\n"
        "🚬 /gop (reply) — наеду на того, кого реплайнул\\n"
        "🚬 @kolyan_byrbot — упомяни в чате (Guest Mode)\\n\\n"
        "*Стата:*\\n"
        "📊 /gop_stats — статистика в чате\\n"
        "📋 /gop_my_nick — текущая кличка\\n"
        "🏅 /gop_achievements — все ачивки\\n\\n"
        "*Управление:*\\n"
        "🔄 /gop_reset — сбросить эскалацию\\n"
        "🔇 /gop_stop — отписаться от наездов\\n"
        "👋 /gop_resume — снова разрешить\\n"
        "❓ /gop_help — эта справка\\n\\n"
        "*Уровни наезда (автоматически):*\\n"
        "1️⃣ Подкат — лёгкий намёк\\n"
        "2️⃣ Наезд — прямое давление\\n"
        "3️⃣ Добор — не отстаём\\n"
        "4️⃣ Проработка — разбираем по косточкам\\n"
        "5️⃣ Ультиматум — последний шанс"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Меню", callback_data="menu_main")],
    ])

    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(help_text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# /gop_achievements — list achievements (editable)
# ---------------------------------------------------------------------------
async def cmd_gop_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id
    all_ach = ach_engine.get_all()
    unlocked = db.get_user_achievements(user["tg_id"], chat_id)
    unlocked_ids = {a["achievement_id"] for a in unlocked}

    lines = ["🏅 *Ачивки гопника*\n"]
    for a in all_ach:
        if a["id"] in unlocked_ids:
            lines.append(f"✅ {a['icon']} {a['title']} — {a['desc']}")
        else:
            lines.append(f"🔒 {a['icon']} {a['title']} — {a['desc']}")

    text = "\n".join(lines)
    keyboard = build_achievements_keyboard(user["tg_id"])

    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# /gop_style — choose style (currently only гопник)
# ---------------------------------------------------------------------------
async def cmd_gop_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚬 Гопник (дефолт)", callback_data="style:gopnik")],
    ])

    await update.message.reply_text("Выбери стиль наезда:", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Callback handler — all inline button actions
# ---------------------------------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data
    chat_id = query.message.chat.id
    user = query.from_user
    user_record = ensure_user(user)
    caller_name = user_record["first_name"] or user_record["username"] or "братан"

    # ── MAIN MENU ──
    if data == "menu_main":
        await query.edit_message_text(
            MENU_TEXT.format(), parse_mode="Markdown", reply_markup=build_menu_keyboard(chat_id)
        )

    # ── HELP ──
    elif data == "menu_help":
        await query.edit_message_text(
            "🚬 *Колян-бот — команды*\n\n"
            "*Наезды:*\\n"
            "🚬 /gop — наеду на тебя\\n"
            "🚬 /gop @username — наеду на юзера\\n"
            "🚬 @kolyan_byrbot — упомяни в чате\\n\\n"
            "*Стата:*\\n"
            "📊 /gop_stats — твоя статистика\\n"
            "📋 /gop_my_nick — кличка\\n"
            "🏅 /gop_achievements — ачивки\\n\\n"
            "*Управление:*\\n"
            "🔄 /gop_reset — сбросить эскалацию\\n"
            "🔇 /gop_stop — отписаться\\n"
            "👋 /gop_resume — разрешить\\n\\n"
            "*Уровни:*\\n"
            "1️⃣ Подкат → 2️⃣ Наезд → 3️⃣ Добор → 4️⃣ Проработка → 5️⃣ Ультиматум",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("← Меню", callback_data="menu_main")],
            ]),
        )

    # ── SELF GOP ──
    elif data == "menu_gop_self":
        # Find self in escalation or use caller
        state = db.get_escalation(chat_id, user.id)
        target_id = user.id
        nickname = db.get_nickname(user.id, chat_id)
        history = db.get_recent_messages(chat_id, user.id, limit=50)
        level = state["level"] if state else 1

        response_text = await llm.gop(
            victim_name=caller_name,
            nickname=nickname,
            escalation_level=level,
            history=history,
            caller_name=caller_name,
            self_gop=True,
        )

        keyboard = build_gop_keyboard(target_id)
        await query.edit_message_text(response_text, reply_markup=keyboard)

        db.add_message(chat_id=chat_id, user_id=user.id, role="gop", text=response_text)
        db.update_escalation(chat_id, user.id, level=level)

        new_ach = ach_engine.check_all(user.id, chat_id)
        for ach in new_ach:
            await query.message.reply_text(f"🏅 {ach['icon']} {ach['title']} — {ach['desc']}")

    # ── MENU STATS ──
    elif data == "menu_stats":
        await cmd_gop_stats(update, context)

    # ── MENU ACHIEVEMENTS ──
    elif data == "menu_achievements":
        await cmd_gop_achievements(update, context)

    # ── MENU MY NICK ──
    elif data == "menu_my_nick":
        await cmd_gop_my_nick(update, context)

    # ── MENU RESET ──
    elif data == "menu_reset":
        await cmd_gop_reset(update, context)

    # ── MENU STOP ──
    elif data == "menu_stop":
        await cmd_gop_stop(update, context)

    # ── GOP AGAIN: re-trigger on target ──
    elif data.startswith("gop_again:"):
        target_id = int(data.split(":")[1])
        state = db.get_escalation(chat_id, target_id)
        current_level = state["level"] if state else 1
        history = db.get_recent_messages(chat_id, target_id, limit=50)
        nickname = db.get_nickname(target_id, chat_id)

        victim_name = caller_name  # callback runs server-side, no TG API access

        response_text = await llm.gop(
            victim_name=victim_name,
            nickname=nickname,
            escalation_level=current_level,
            history=history,
            caller_name=caller_name,
            self_gop=(target_id == user.id),
        )

        keyboard = build_gop_keyboard(target_id)
        await query.edit_message_text(response_text, reply_markup=keyboard)

        db.add_message(chat_id=chat_id, user_id=target_id, role="gop", text=response_text)

        new_ach = ach_engine.check_all(user.id, chat_id)
        for ach in new_ach:
            await query.message.reply_text(f"🏅 {ach['icon']} {ach['title']} — {ach['desc']}")

    # ── GOP REPLY (inline hint) ──
    elif data.startswith("gop_reply:"):
        target_id = int(data.split(":")[1])
        state = db.get_escalation(chat_id, target_id)
        lvl = state["level"] if state else 1
        await query.edit_message_text(
            f"💬 Чтобы продолжить — *реплайни* моё сообщение своим текстом.\\n\\n"
            f"Текущий уровень: {lvl}/5\\n\\n"
            "Или нажми '← Меню' для навигации.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("← Меню", callback_data="menu_main")],
            ]),
        )

    # ── STATS BUTTON ──
    elif data.startswith("stats_btn:") or data == "menu_stats":
        target_id = int(data.split(":")[1]) if ":" in data else user.id
        stats = db.get_user_stats(target_id, chat_id)
        nickname = db.get_nickname(target_id, chat_id)
        state = db.get_escalation(chat_id, target_id)

        nickname_str = f"\n📋 Кличка: {nickname}" if nickname else ""
        level_str = f"\n📈 Уровень: {state['level']}/5" if state else "\n📈 Уровень: не начал"

        text = (
            f"📊 *Статистика*\n\n"
            f"🚬 Наезжал: {stats['times_called']} раз\n"
            f"💀 На тебя наезжали: {stats['times_gopped']} раз\n"
            f"🏆 Уважуха: {stats['respect_earned']} раз{nickname_str}{level_str}"
        )
        keyboard = build_stats_keyboard(chat_id, target_id)

        if data == "menu_stats":
            target_id = user.id
            stats = db.get_user_stats(user.id, chat_id)
            nickname = db.get_nickname(user.id, chat_id)
            state = db.get_escalation(chat_id, user.id)
            nickname_str = f"\n📋 Кличка: {nickname}" if nickname else ""
            level_str = f"\n📈 Уровень: {state['level']}/5" if state else "\n📈 Уровень: не начал"
            text = (
                f"📊 *Статистика*\n\n"
                f"🚬 Наезжал: {stats['times_called']} раз\n"
                f"💀 На тебя наезжали: {stats['times_gopped']} раз\n"
                f"🏆 Уважуха: {stats['respect_earned']} раз{nickname_str}{level_str}"
            )
            keyboard = build_stats_keyboard(chat_id, user.id)
        else:
            pass

        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    # ── ACHIEVEMENTS BUTTON ──
    elif data.startswith("ach_btn:") or data == "menu_achievements":
        target_id = int(data.split(":")[1]) if ":" in data else user.id
        all_ach = ach_engine.get_all()
        unlocked = db.get_user_achievements(target_id, chat_id)
        unlocked_ids = {a["achievement_id"] for a in unlocked}

        lines = ["🏅 *Ачивки*\n"]
        for a in all_ach:
            if a["id"] in unlocked_ids:
                lines.append(f"✅ {a['icon']} {a['title']} — {a['desc']}")
            else:
                lines.append(f"🔒 {a['icon']} {a['title']} — {a['desc']}")

        text = "\n".join(lines)
        keyboard = build_achievements_keyboard(target_id)

        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    # ── STYLE PICKER ──
    elif data.startswith("style:"):
        style = data.split(":")[1]
        db.set_style(user.id, chat_id, style)
        style_names = {"gopnik": "🚬 Гопник"}
        await query.edit_message_text(f"Стиль: {style_names.get(style, style)}")

    # ── UNRECOGNIZED ──
    else:
        await query.answer("Эта кнопка пока не работает 😬")


# ---------------------------------------------------------------------------
# Guest message handler
# ---------------------------------------------------------------------------
async def handle_guest_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle guest bot mentions — Bot API 10.0 Guest Mode."""
    if not update.guest_message:
        return

    guest_msg = update.guest_message
    guest_query_id = guest_msg.guest_query_id
    chat_id = guest_msg.chat_id if guest_msg.chat else None

    user = guest_msg.from_user
    text = guest_msg.text or guest_msg.caption or ""

    # Strip @mentions
    text = re.sub(r'@\w+', '', text).strip()

    logger.info(f"[GUEST] user={user.id} ({user.first_name}), chat={chat_id}, query_id={guest_query_id}, text='{text[:80]}'")

    # === COOLDOWN CHECK ===
    if chat_id:
        last_resp = db.get_cooldown(chat_id)
        if last_resp:
            try:
                elapsed = (datetime.now(timezone.utc) - last_resp.replace(tzinfo=timezone.utc)).total_seconds()
            except Exception:
                elapsed = 999
            if elapsed < 5:
                logger.info(f"[GUEST] cooldown active ({elapsed:.1f}s < 5s), ignoring")
                try:
                    result = InlineQueryResultArticle(
                        id="cooldown",
                        title="(Кулдаун)",
                        input_message_content=InputTextMessageContent(""),
                    )
                    await context.bot.answer_guest_query(guest_query_id, result)
                except Exception:
                    pass
                return

    # Check blacklist
    if chat_id and db.is_blacklisted(user.id, chat_id):
        try:
            result = InlineQueryResultArticle(
                id="noop",
                title="Отписался от наездов",
                input_message_content=InputTextMessageContent(""),
            )
            await context.bot.answer_guest_query(guest_query_id, result)
        except Exception as e:
            logger.error(f"[GUEST] failed to answer blacklist: {e}")
        return

    user_record = ensure_user(user)
    caller_name = user_record["first_name"] or user_record["username"] or "братан"

    # === ESCALATION ===
    state = db.get_escalation(chat_id, user.id) if chat_id else None
    current_level = state["level"] if state else 1
    call_count = state.get("message_count", 0) if state else 0

    # === NICKNAME ===
    nickname = None
    if chat_id:
        nickname = db.get_nickname(user.id, chat_id)

    if not nickname:
        try:
            nickname = await asyncio.wait_for(
                llm.generate_nickname(caller_name, context=text or "упомянул Колю"),
                timeout=2.0,
            )
            if chat_id:
                db.update_nickname(user.id, chat_id, nickname)
        except Exception as e:
            logger.warning(f"[GUEST] nickname gen failed: {e}")
            fallback_nicks = ["Лох", "Чушок", "Ссыкло", "Тормоз", "Шнырь", "Фуфло", "Балабол", "Хмырь"]
            nickname = random.choice(fallback_nicks)
            if chat_id:
                db.update_nickname(user.id, chat_id, nickname)

    # === CONTEXT ===
    context_text = ""
    if guest_msg.reply_to_message and guest_msg.reply_to_message.from_user:
        reply_user = guest_msg.reply_to_message.from_user
        if not reply_user.is_bot:
            victim_record = ensure_user(reply_user)
            context_text = guest_msg.reply_to_message.text or guest_msg.reply_to_message.caption or ""

    # === LLM CALL ===
    response_text = None
    try:
        level_prompts = {
            1: "Ты — реальный гопник. Подкат — лёгкий намёк, грубовато. 1-3 фразы, 250 символов.",
            2: "Ты — гопник. Прямой наезд — жёстче. 1-3 фразы, 250 символов.",
            3: "Ты — гопник. Добор — не отстаёшь. 2-4 фразы, 300 символов.",
            4: "Ты — гопник. Проработка — разбираешь по косточкам. 2-4 фразы, 300 символов.",
            5: "Ты — гопник. Ультиматум — последний шанс. 1-2 фразы, 200 символов.",
        }
        level = min(current_level, 5)
        system_prompt = level_prompts.get(level, level_prompts[1])

        user_parts = []
        if nickname:
            user_parts.append(f"Кличка: {nickname}")
        if call_count > 0:
            user_parts.append(f"Вызывали уже {call_count} раз(а), уровень {level}/5.")
        if context_text:
            user_parts.append(f"Он упомянул тебя: «{context_text[:200]}»")
        if text:
            user_parts.append(f"Он написал: «{text}»")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(user_parts) if user_parts else "Наедь."},
        ]
        response_text = await asyncio.wait_for(
            llm._call_api_async(messages, max_tokens=180, temperature=1.0),
            timeout=8.0,
        )
        if response_text:
            response_text = response_text.strip().strip('"\'').strip()
            for prefix in ["Вот ответ:", "Наезд:", "Ответ:", "Вот наезд:", "Гопник:"]:
                if response_text.lower().startswith(prefix.lower()):
                    response_text = response_text[len(prefix):].strip()
            response_text = re.sub(r'@(\w+)', r'\1', response_text)
            response_text = response_text.strip()
            logger.info(f"[GUEST] LLM response (level {level}, call #{call_count+1}): '{response_text[:60]}...'")

    except asyncio.TimeoutError:
        logger.warning(f"[GUEST] LLM timeout at level {level}")
    except Exception as e:
        logger.warning(f"[GUEST] LLM error: {e}")

    # === FALLBACK ===
    if not response_text or len(response_text.strip()) < 5:
        fallback_pool = {
            1: [
                f"О, {nickname or caller_name} тут как тут. Ну чё, сам пришёл или как?",
                f"Э, {nickname or caller_name}, ты чё забыл тут?",
                f"Смотрите, {nickname or caller_name} пожаловал.",
                f"Хм, {nickname or caller_name}, ну давай, рассказывай.",
                f"Оп-па, {nickname or caller_name} нарисовался. Базар есть?",
            ],
            2: [
                f"Ты чё, {nickname or caller_name}, нарываешься?",
                f"{nickname or caller_name}, на районе так не катит.",
                f"Слышь, {nickname or caller_name}, кто тут главный?",
                f"{nickname or caller_name}, за такие замашки быстро ставят на место.",
            ],
            3: [
                f"Всё, {nickname or caller_name}, конкретно заебал.",
                f"{nickname or caller_name}, тебя уже {call_count+1} раз предупреждаю.",
                f"Э, {nickname or caller_name}, ты реально такой тупой?",
            ],
            4: [
                f"{nickname or caller_name}, даже базар построить не можешь.",
                f"Слушай сюда, {nickname or caller_name} — весь район знает тебя как лоха.",
                f"{nickname or caller_name}, посмотри на себя — ты даже отвечать не умеешь.",
            ],
            5: [
                f"Всё, {nickname or caller_name}, последний раз. Либо докажешь, либо вали.",
                f"Ну чё, {nickname or caller_name}, один шанс. Докажи что не лох.",
                f"Последний базар, {nickname or caller_name}: говори кто ты, или проваливай.",
            ],
        }
        pool = fallback_pool.get(level, fallback_pool[1])
        hash_val = int(hashlib.sha256((text or "").encode()).hexdigest(), 16)
        idx = (call_count + hash_val) % len(pool)
        response_text = pool[idx]
        response_text = re.sub(r'@(\w+)', r'\1', response_text)

    # === SEND GUEST RESPONSE ===
    result = InlineQueryResultArticle(
        id=f"gop_{current_level}",
        title=f"🚬 Наехать (уровень {level})",
        description=response_text[:80] + "..." if len(response_text) > 80 else response_text,
        input_message_content=InputTextMessageContent(
            message_text=response_text + f"\n\n🚬 [{call_count+1}-й наезд] | Ур: {level}/5" + (f" | {nickname}" if nickname else ""),
        ),
    )

    try:
        await context.bot.answer_guest_query(guest_query_id, result)
        logger.info(f"[GUEST] sent: '{response_text[:50]}...'")

        if chat_id:
            db.set_cooldown(chat_id)
            db.increment_gopped(user.id, chat_id)
            db.increment_called(user.id, chat_id)
            db.add_message(chat_id=chat_id, user_id=user.id, role="user", text=text)
            db.add_message(chat_id=chat_id, user_id=user.id, role="gop", text=response_text)

            new_level = current_level
            new_count = call_count + 1
            if new_count >= 2 and current_level < 5:
                new_level = current_level + 1
            db.update_escalation(chat_id, user.id, level=new_level, message_count=new_count)

            new_achievements = ach_engine.check_all(user.id, chat_id)
            for ach in new_achievements:
                logger.info(f"[GUEST] achievement unlocked for {user.id}: {ach['title']}")

    except Exception as e:
        logger.error(f"[GUEST] failed to answer: {e}")


# ---------------------------------------------------------------------------
# Mention handler
# ---------------------------------------------------------------------------
async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle @kolyan_byrbot mentions."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text or update.message.caption or ""
    is_dm = update.effective_chat.type == "private"

    if not is_dm:
        bot_username = context.bot.username.lower()
        mentioned = False
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == "mention":
                    mention_text = text[entity.offset:entity.offset + entity.length].lower()
                    if mention_text == f"@{bot_username}":
                        mentioned = True
                        break
        if not mentioned:
            return

    bot_username = context.bot.username
    clean_text = re.sub(rf"@{bot_username}\b", "", text).strip()

    logger.info(f"[MENTION] user={user.id} ({user.first_name}), chat={chat_id}, is_dm={is_dm}, text='{clean_text[:80]}'")

    if db.is_blacklisted(user.id, chat_id):
        return

    user_record = ensure_user(user)
    caller_name = user_record["first_name"] or user_record["username"] or "братан"

    state = db.get_escalation(chat_id, user.id)
    current_level = state["level"] if state else 1

    history = db.get_recent_messages(chat_id, user.id, limit=50)
    nickname = db.get_nickname(user.id, chat_id)

    response_text = await llm.gop(
        victim_name=caller_name,
        nickname=nickname,
        escalation_level=current_level,
        history=history,
        victim_context=clean_text,
        caller_name=caller_name,
        self_gop=True,
    )

    sent = await update.message.reply_text(response_text)

    db.add_message(chat_id=chat_id, user_id=user.id, role="user", text=text)
    db.add_message(chat_id=chat_id, user_id=user.id, role="gop", text=response_text)
    db.save_gop_message(sent.message_id, chat_id, user.id, current_level)

    db.increment_gopped(user.id, chat_id)
    db.update_escalation(chat_id, user.id, level=current_level)

    new_achievements = ach_engine.check_all(user.id, chat_id)
    for ach in new_achievements:
        await update.message.reply_text(
            f"🏅 Ачивка: {ach['icon']} {ach['title']} — {ach['desc']}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("gop", cmd_gop))
    app.add_handler(CommandHandler("gop_stop", cmd_gop_stop))
    app.add_handler(CommandHandler("gop_resume", cmd_gop_resume))
    app.add_handler(CommandHandler("gop_stats", cmd_gop_stats))
    app.add_handler(CommandHandler("gop_achievements", cmd_gop_achievements))
    app.add_handler(CommandHandler("gop_style", cmd_gop_style))
    app.add_handler(CommandHandler("gop_reset", cmd_gop_reset))
    app.add_handler(CommandHandler("gop_my_nick", cmd_gop_my_nick))
    app.add_handler(CommandHandler("gop_help", cmd_gop_help))

    # Callbacks — ALL inline buttons go through handle_callback
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(menu_|gop_|stats_|ach_|style:|help)"))

    # Guest Mode
    app.add_handler(MessageHandler(filters.UpdateType.GUEST_MESSAGE, handle_guest_message))

    # Mention handler
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND & filters.Entity("mention"),
        handle_mention
    ))
    # DM mentions
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_mention
    ))

    # Reply handler
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND & filters.REPLY, handle_reply))

    logger.info("🚬 Колян-бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
