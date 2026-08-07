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
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
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
# /start — welcome
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚬 Ну чё, наехал? Я — Колян. Пиши /gop — наеду на тебя. "
        "Или /gop @username — наеду на кого надо. "
        "Или просто упомяни @kolyan_byrbot в чате — я отвечу.\n\n"
        "/gop_stop — отписаться от наездов\n"
        "/gop_resume — снова разрешить наезды\n"
        "/gop_stats — твоя статистика\n"
        "/gop_achievements — ачивки\n"
        "/gop_style — выбрать стиль (пока только гопник)"
    )


# ---------------------------------------------------------------------------
# /gop — main command
# ---------------------------------------------------------------------------
async def cmd_gop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main gop command. /gop or /gop @username"""
    chat_id = update.effective_chat.id
    caller = update.effective_user
    caller_record = ensure_user(caller)

    # Check if caller is blacklisted (opted out)
    if db.is_blacklisted(caller.id, chat_id):
        await update.message.reply_text("Ты сам отписался от наездов. /gop_resume если передумал.")
        return

    # Determine victim
    victim_record = None
    victim_context = None
    reply_to_message = update.message.reply_to_message

    if context.args and context.args[0].startswith("@"):
        # /gop @username
        victim_username = context.args[0].lstrip("@")
        # Try to find this user in our DB
        victim_record = db.find_user_by_username(victim_username)
        if not victim_record:
            # We don't know this user yet — oneshot mode
            victim_name = victim_username
        else:
            victim_name = victim_record["first_name"] or victim_record["username"] or victim_username
    elif reply_to_message and reply_to_message.from_user:
        # /gop in reply to someone's message
        victim = reply_to_message.from_user
        victim_record = ensure_user(victim)
        victim_name = victim_record["first_name"] or victim_record["username"] or "лох"
        victim_context = reply_to_message.text or reply_to_message.caption or ""
    else:
        # /gop with no args — self-gop
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

    # Get conversation history (last 50 messages)
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

    # Send response
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💪 Ответить", callback_data=f"gop_reply:{target_id if target_id else caller.id}"),
            InlineKeyboardButton("🚬 Ещё наехать", callback_data=f"gop_again:{target_id if target_id else caller.id}"),
            InlineKeyboardButton("📊 Стата", callback_data=f"gop_stats_btn:{target_id if target_id else caller.id}"),
        ]
    ])
    sent = await update.message.reply_text(response_text, reply_markup=keyboard)

    # Save message to history
    db.add_message(
        chat_id=chat_id,
        user_id=target_id if target_id else caller.id,
        role="gop",
        text=response_text,
    )
    # Save gop message for reply tracking
    if sent and target_id:
        db.save_gop_message(sent.message_id, chat_id, target_id, current_level)

    # Update escalation
    if target_id:
        db.update_escalation(chat_id, target_id, level=current_level)

    # Check achievements for caller
    new_achievements = ach_engine.check_all(caller.id, chat_id)
    for ach in new_achievements:
        await update.message.reply_text(
            f"🏅 Ачивка разблокирована: {ach['icon']} {ach['title']} — {ach['desc']}"
        )

    # Check achievements for victim
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
    # Only process if replying to a bot message
    if not reply_to.from_user or not reply_to.from_user.is_bot:
        return

    # Check if the bot message was a gop message
    gop_msg = db.get_gop_message(reply_to.message_id, update.effective_chat.id)
    if not gop_msg:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    user_record = ensure_user(user)

    if db.is_blacklisted(user.id, chat_id):
        return

    # Get escalation state for this user
    state = db.get_escalation(chat_id, user.id)
    if not state:
        return

    current_level = state["level"]

    # Get history
    history = db.get_recent_messages(chat_id, user.id, limit=50)
    nickname = db.get_nickname(user.id, chat_id)

    # Analyze the user's reply to determine escalation direction
    user_text = update.message.text or ""
    response_text = await llm.gop_reply(
        victim_name=user_record["first_name"] or user_record["username"] or "братан",
        nickname=nickname,
        current_level=current_level,
        user_reply=user_text,
        history=history,
    )

    sent = await update.message.reply_text(response_text)

    # Save both messages
    db.add_message(chat_id=chat_id, user_id=user.id, role="user", text=user_text)
    db.add_message(chat_id=chat_id, user_id=user.id, role="gop", text=response_text)

    # Determine new level
    new_level = llm.determine_escalation(user_text, current_level)
    db.update_escalation(chat_id, user.id, level=new_level)

    # Update nickname based on level
    if new_level >= 6:  # Уважуха
        db.update_nickname(user.id, chat_id, None)  # Remove nickname — earned respect
    elif nickname is None and current_level < 6:
        # Assign a new insult nickname if they don't have one
        nicknames = ["Лох", "Чушок", "Ссыкло", "Тормоз", "Шнырь", "Фуфло", "Балабол", "Штрих"]
        db.update_nickname(user.id, chat_id, random.choice(nicknames))

    # Check achievements
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
    await update.message.reply_text("Ну всё, отстал от тебя. Пока. /gop_resume — если передумаешь.")


# ---------------------------------------------------------------------------
# /gop_resume — opt back in
# ---------------------------------------------------------------------------
async def cmd_gop_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id
    db.remove_from_blacklist(user["tg_id"], chat_id)
    await update.message.reply_text("О, вернулся? Ну держись, лох. Теперь я снова наеду.")


# ---------------------------------------------------------------------------
# /gop_stats — statistics
# ---------------------------------------------------------------------------
async def cmd_gop_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id
    stats = db.get_user_stats(user["tg_id"], chat_id)
    nickname = db.get_nickname(user["tg_id"], chat_id)
    state = db.get_escalation(chat_id, user["tg_id"])

    nickname_str = f"\n📋 Кличка: {nickname}" if nickname else ""
    level_str = f"\n📈 Уровень: {state['level']}/7" if state else "\n📈 Уровень: не начал"

    text = (
        f"📊 Статистика гопника\n\n"
        f"🚬 Наезжал: {stats['times_called']} раз\n"
        f"💀 На тебя наезжали: {stats['times_gopped']} раз\n"
        f"🏆 Уважуха: {stats['respect_earned']} раз{nickname_str}{level_str}\n\n"
        f"Ачивки: /gop_achievements"
    )
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# /gop_reset — reset own escalation
# ---------------------------------------------------------------------------
async def cmd_gop_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    db.reset_escalation(chat_id, user.id)
    db.update_nickname(user.id, chat_id, None)

    await update.message.reply_text(
        "🔄 Сбросил твою эскалацию и кличку в этом чате. Начнём с чистого листа."
    )


# ---------------------------------------------------------------------------
# /gop_my_nick — show my current nickname
# ---------------------------------------------------------------------------
async def cmd_gop_my_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    nickname = db.get_nickname(user.id, chat_id)

    if nickname:
        await update.message.reply_text(f"📋 Твоя кличка в этом чате: *{nickname}*\n\nСбросить: /gop_reset")
    else:
        await update.message.reply_text("📋 У тебя пока нет клички. Наезди на меня хоть раз — получишь.")


# ---------------------------------------------------------------------------
# /gop_help — show all commands
# ---------------------------------------------------------------------------
async def cmd_gop_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚬 *Колян-бот — команды*\n\n"
        "*Наезды:*\n"
        "/gop — наеду на тебя\n"
        "/gop @username — наеду на юзера\n"
        "/gop (reply) — наеду на того, кого реплайнул\n"
        "@kolyan_byrbot — упомяни в любом чате, я наеду (guest mode)\n\n"
        "*Настройки:*\n"
        "/gop\\_style — выбрать стиль наезда\n"
        "/gop\\_reset — сбросить эскалацию и кличку в чате\n\n"
        "*Стата:*\n"
        "/gop\\_stats — твоя статистика в чате\n"
        "/gop\\_my\\_nick — твоя текущая кличка\n"
        "/gop\\_achievements — все ачивки\n\n"
        "*Управление:*\n"
        "/gop\\_stop — отписаться от наездов\n"
        "/gop\\_resume — снова разрешить наезды\n"
        "/gop\\_help — эта справка"
    )


# ---------------------------------------------------------------------------
# /gop_achievements — list achievements
# ---------------------------------------------------------------------------
async def cmd_gop_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id
    all_ach = ach_engine.get_all()
    unlocked = db.get_user_achievements(user["tg_id"], chat_id)
    unlocked_ids = {a["achievement_id"] for a in unlocked}

    lines = ["🏅 Ачивки гопника:\n"]
    for a in all_ach:
        if a["id"] in unlocked_ids:
            lines.append(f"✅ {a['icon']} {a['title']} — {a['desc']}")
        else:
            lines.append(f"🔒 {a['icon']} {a['title']} — {a['desc']}")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /gop_style — choose style (currently only гопник)
# ---------------------------------------------------------------------------
async def cmd_gop_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    chat_id = update.effective_chat.id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚬 Гопник (дефолт)", callback_data="style:gopnik")],
        # Future: more styles
    ])

    await update.message.reply_text("Выбери стиль наезда:", reply_markup=keyboard)


async def handle_style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("style:"):
        style = data.split(":")[1]
        user = ensure_user(query.from_user)
        chat_id = query.message.chat.id
        db.set_style(user["tg_id"], chat_id, style)
        style_names = {"gopnik": "🚬 Гопник"}
        await query.edit_message_text(f"Стиль установлен: {style_names.get(style, style)}")
    elif data.startswith("gop_again:"):
        # Re-trigger gop on same target
        target_id = int(data.split(":")[1])
        caller = query.from_user
        chat_id = query.message.chat.id

        # Get victim info
        victim_record = db.find_user_by_id(target_id) if hasattr(db, "find_user_by_id") else None
        if not victim_record:
            # Use any available info
            victim_name = "этот"
        else:
            victim_name = victim_record.get("first_name") or victim_record.get("username") or "этот"

        # Get escalation state
        state = db.get_escalation(chat_id, target_id)
        current_level = state["level"] if state else 1
        history = db.get_recent_messages(chat_id, target_id, limit=50)
        nickname = db.get_nickname(target_id, chat_id)

        # Generate response
        response_text = await llm.gop(
            victim_name=victim_name,
            nickname=nickname,
            escalation_level=current_level,
            history=history,
            caller_name=caller.first_name or "кто-то",
            self_gop=False,
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💪 Ответить", callback_data=f"gop_reply:{target_id}"),
                InlineKeyboardButton("🚬 Ещё наехать", callback_data=f"gop_again:{target_id}"),
                InlineKeyboardButton("📊 Стата", callback_data=f"gop_stats_btn:{target_id}"),
            ]
        ])
        sent = await query.message.reply_text(response_text, reply_markup=keyboard)
        db.add_message(chat_id=chat_id, user_id=target_id, role="gop", text=response_text)
        if sent:
            db.save_gop_message(sent.message_id, chat_id, target_id, current_level)
    elif data.startswith("gop_reply:"):
        target_id = int(data.split(":")[1])
        await query.answer()
        await query.message.reply_text(
            f"💬 Ответь на сообщение бота — я подхвачу и продолжу наезжать. Текущий уровень: {db.get_escalation(query.message.chat.id, target_id)['level']}/5"
        )
    elif data.startswith("gop_stats_btn:"):
        target_id = int(data.split(":")[1])
        chat_id = query.message.chat.id
        stats = db.get_user_stats(target_id, chat_id)
        nickname = db.get_nickname(target_id, chat_id)
        state = db.get_escalation(chat_id, target_id)

        nickname_str = f"\n📋 Кличка: {nickname}" if nickname else ""
        level_str = f"\n📈 Уровень: {state['level']}/5" if state else "\n📈 Уровень: не начал"

        text = (
            f"📊 Статистика лоха\n\n"
            f"🚬 Наезжал на других: {stats['times_called']} раз\n"
            f"💀 На него наезжали: {stats['times_gopped']} раз\n"
            f"🏆 Уважуха: {stats['respect_earned']} раз{nickname_str}{level_str}"
        )
        await query.answer(text, show_alert=True)





async def handle_guest_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle guest bot mentions — bot responds in chat it's not a member of.

    Bot API 10.0 Guest Mode feature (May 2026). Bot can mention @kolyan_byrbot
    in ANY chat and respond as itself.

    Improvements:
    - Quality LLM responses with escalating gop styles (level 1-5)
    - Persistent nicknames (insult based on user_id+chat_id)
    - Escalation tracking (gops get harder with repeated calls)
    - Per-chat stats
    """
    if not update.guest_message:
        return

    guest_msg = update.guest_message
    guest_query_id = guest_msg.guest_query_id
    chat_id = guest_msg.chat_id if guest_msg.chat else None

    user = guest_msg.from_user
    text = guest_msg.text or guest_msg.caption or ""

    # Strip own mention from incoming text — LLM shouldn't see "tell me about @kolyan_byrbot"
    # Also strips other @mentions to prevent bot ping-pong loops
    text = re.sub(r'@\w+', '', text).strip()

    logger.info(f"[GUEST] user={user.id} ({user.first_name}), chat={chat_id}, query_id={guest_query_id}, text='{text[:80]}'")

    # === COOLDOWN CHECK — prevent bot ping-pong and flood ===
    # If someone (e.g. another guest bot) pinged us < 5 sec ago, ignore this mention
    if chat_id:
        last_resp = db.get_cooldown(chat_id)
        if last_resp:
            elapsed = (datetime.now(timezone.utc) - last_resp.replace(tzinfo=timezone.utc)).total_seconds()
            if elapsed < 5:
                logger.info(f"[GUEST] cooldown active ({elapsed:.1f}s < 5s), ignoring mention from {user.id}")
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

    # === ESCALATION TRACKING ===
    # Get current escalation level and call count
    state = db.get_escalation(chat_id, user.id) if chat_id else None
    current_level = state["level"] if state else 1
    call_count = state.get("message_count", 0) if state else 0

    # Cap at level 5 — beyond that bot gets bored / gives respect
    # Levels: 1=подкат, 2=наезд, 3=добор, 4=проработка, 5=ультиматум

    # === NICKNAME ===
    # Get or generate nickname (cached per user+chat)
    nickname = None
    if chat_id:
        nickname = db.get_nickname(user.id, chat_id)

    if not nickname:
        # Generate one
        try:
            nickname = await asyncio.wait_for(
                llm.generate_nickname(caller_name, context=text or "упомянул Колю"),
                timeout=2.0,
            )
            if chat_id:
                db.update_nickname(user.id, chat_id, nickname)
        except Exception as e:
            logger.warning(f"[GUEST] nickname gen failed: {e}")
            # Fallback nicknames
            fallback_nicks = ["Лох", "Чушок", "Ссыкло", "Тормоз", "Шнырь", "Фуфло", "Балабол", "Хмырь"]
            nickname = random.choice(fallback_nicks)
            if chat_id:
                db.update_nickname(user.id, chat_id, nickname)

    # === CONTEXT ===
    context_text = ""
    victim_name = None
    if guest_msg.reply_to_message and guest_msg.reply_to_message.from_user:
        reply_user = guest_msg.reply_to_message.from_user
        if not reply_user.is_bot:
            victim_record = ensure_user(reply_user)
            victim_name = victim_record["first_name"] or victim_record["username"] or "этот"
            context_text = guest_msg.reply_to_message.text or guest_msg.reply_to_message.caption or ""
        else:
            context_text = text
    else:
        context_text = text

    # === BUILD PROMPT WITH ESCALATION ===
    # Choose prompt based on level — full quality gop prompts
    level_prompts = {
        1: """Ты — реальный гопник с района, районный хулиган 90-х. Только что встретил этого лоха. Подкат — не агрессивный, но уже с наездом. Коротко, грубовато, с дворовым сленгом.

ПРАВИЛА:
- Обращайся по кличке (она дана)
- Если есть его текст — ОБЯЗАТЕЛЬНО подколи его, зацепись за конкретное слово или фразу
- 1-3 коротких фразы, максимум 250 символов
- Маты (блядь, нахуй, хуй, пиздец) — разрешены и желательны
- Сленг: район, базар, рамсы, подъезд, кент, пацан, шестёрка, стрелка
- НЕ извиняйся, НЕ будь вежливым
- НЕ пиши «привет» или другие нейтральные слова — сразу наезд""",

        2: """Ты — реальный гопник с района. Этот лох не понял подката. Теперь прямой наезд — жёстче, конкретнее. Ты уже не присматриваешься, ты наезжаешь. Обзывай, тычь в слабые места.

ПРАВИЛА:
- Обращайся по кличке
- Если есть его текст — РАЗБЕРИ его по косточкам, подколи каждое слово
- Можешь угрожать — «приеду разберусь», «по ебалу получишь», «в подъезде подожду»
- 1-3 фразы, максимум 250 символов
- Больше мата, жёстче тон
- НЕ извиняйся""",

        3: """Ты — гопник с района, добор. Лох пытается отбиться, но ты не отстаёшь. Достаёшь его, прикалываешься, не даёшь уйти от разговора.

ПРАВИЛА:
- Обращайся по кличке
- Если есть его текст — высмей каждую фразу, передразни
- Издёвка, сарказм, мат через слово
- 2-4 фразы, максимум 300 символов
- Цель — довести до белого каления
- НЕ извиняйся""",

        4: """Ты — гопник с района, проработка. Разбираешь каждое слово лоха. Сарказм, подкол на каждом шагу. Ты как следователь — ищешь слабые места и бьёшь по ним.

ПРАВИЛА:
- Обращайся по кличке
- Если есть его текст — препарируй его фразу, найди логические дыры
- Ты умнее и жёстче
- 2-4 фразы, максимум 300 символов
- Мат обязателен
- НЕ извиняйся""",

        5: """Ты — гопник с района, ультиматум. Последний шанс для лоха. Либо он докажет что не лох, либо ты отвалишь и запомнишь его как слабака.

ПРАВИЛА:
- Обращайся по кличке
- Если есть его текст — последний раз вразумь
- Констатируй факты, не умоляй
- 1-2 фразы, максимум 200 символов
- Тон: достоинство, не злость
- НЕ извиняйся""",
    }
    level = min(current_level, 5)
    system_prompt = level_prompts.get(level, level_prompts[1])

    # Build user message with FULL context — LLM should use everything
    user_parts = []
    if nickname:
        user_parts.append(f"Кличка этого лоха: {nickname}")
    if call_count > 0:
        user_parts.append(f"Этот лох вызывал тебя уже {call_count} раз(а) в этом чате. Уровень наезда: {level}/5.")
    if context_text and context_text != text:
        # We have a replied-to message with content
        user_parts.append(f"Он упомянул тебя в ответ на чужое сообщение «{context_text[:200] if context_text else ''}»")
    if text:
        user_parts.append(f"Он написал: «{text}»\n\nПодколи конкретно эти слова или фразу. Если он пустословит — покажи это.")
    elif not text and not context_text:
        user_parts.append("Он просто упомянул тебя без слов. Наедь в стиле подката.")

    user_msg = "\n".join(user_parts) if user_parts else "Наедь."

    # === LLM CALL (with timeout for guest mode) ===
    response_text = None
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        response_text = await asyncio.wait_for(
            llm._call_api_async(messages, max_tokens=180, temperature=1.0),
            timeout=8.0,  # Telegram allows ~10s for answer_guest_query
        )
        if response_text:
            # Clean up
            response_text = response_text.strip().strip('"\x27').strip()
            for prefix in ["Вот ответ:", "Наезд:", "Ответ:", "Вот наезд:", "Гопник:"]:
                if response_text.lower().startswith(prefix.lower()):
                    response_text = response_text[len(prefix):].strip()

            # Strip @mentions — prevents guest-bot ping-pong loops
            response_text = re.sub(r'@(\w+)', r'\1', response_text)
            response_text = response_text.strip()

            logger.info(f"[GUEST] LLM response (level {level}, call #{call_count+1}): '{response_text[:60]}...'")
    except asyncio.TimeoutError:
        logger.warning(f"[GUEST] LLM timeout at level {level}")
    except Exception as e:
        logger.warning(f"[GUEST] LLM error: {e}")

     # === FALLBACK IF LLM FAILED ===
    if not response_text or len(response_text.strip()) < 5:
        # Pre-built fallback responses per level — bigger pool, more variety
        # Include the user's text/context in some variants for personalization
        fallback_pool = {
            1: [
                f"О, {nickname or caller_name} тут как тут. Ну чё, сам пришёл или как?",
                f"Э, {nickname or caller_name}, ты чё забыл тут? На районе таких не ждут.",
                f"Смотрите, {nickname or caller_name} пожаловал. Запомню тебя.",
                f"Хм, {nickname or caller_name}, какой сюрприз. Ну давай, расскажи зачем припёрся.",
                f"Оп-па, {nickname or caller_name} нарисовался. Ну чё, базар есть?",
                f"{nickname or caller_name}, ты меня звал? Я тут, но мне уже скучно.",
            ],
            2: [
                f"Ты чё, {nickname or caller_name}, нарываешься? На районе так не катит, тут по понятиям.",
                f"{nickname or caller_name}, ты в курсе, что на районе за такое наказывают?",
                f"Слышь, {nickname or caller_name}, тебе сколько раз объяснять, кто тут главный?",
                f"{nickname or caller_name}, ты чё, борзой? На районе за такие замашки быстро ставят на место.",
                f"Э, {nickname or caller_name}, ты думаешь тут одни лохи? Не, братан, тут такие как ты быстро понимают.",
                f"{nickname or caller_name}, давай конкретнее — что тебе надо? Только без понтов, тут их не любят.",
            ],
            3: [
                f"Всё, {nickname or caller_name}, конкретно заебал. На районе таких быстро в подъезде ставят на место.",
                f"Слышь, {nickname or caller_name}, тебя уже {call_count+1} раз предупреждаю — заткнись или пожалеешь.",
                f"{nickname or caller_name}, ты реально думаешь что я буду тебя терпеть? На районе за это морду бьют.",
                f"Э, {nickname or caller_name}, ты реально такой тупой или прикидываешься?",
                f"{nickname or caller_name}, ты вообще в зеркало на себя смотрел? На районе за такое бьют сразу.",
                f"Слышь, {nickname or caller_name}, тебя тут все уже знают как лоха. Хватит позориться.",
            ],
            4: [
                f"{nickname or caller_name}, ты реально тупой или прикидываешься? Даже базар построить не можешь.",
                f"Слушай сюда, {nickname or caller_name} — ты уже столько раз нарывался, что тебя весь район знает как лоха.",
                f"{nickname or caller_name}, посмотри на себя — ты даже отвечать нормально не умеешь, а туда же, нарываешься.",
                f"Слышь, {nickname or caller_name}, ты чё, в школе на физре всегда последним бегал? По тебе видно.",
                f"{nickname or caller_name}, у тебя базар как у пятиклассника. На районе за такое бьют сразу, без разговоров.",
                f"Э, {nickname or caller_name}, ты реально думаешь что у тебя получится со мной тягаться? Смешно.",
            ],
            5: [
                f"Всё, {nickname or caller_name}, последний раз говорю. Либо ты доказываешь что не ссыкло, либо отваливаю и запомню тебя как лоха навсегда.",
                f"Ну чё, {nickname or caller_name}, давай — докажи, что не пустое место. Один шанс.",
                f"Последний базар, {nickname or caller_name}: говори кто ты и зачем сюда припёрся, или проваливай.",
                f"{nickname or caller_name}, последнее предупреждение. Дальше я уже без базара буду действовать.",
                f"Слышь, {nickname or caller_name}, ты заебал. Либо сейчас доказываешь что не лох, либо я ухожу. Решай.",
                f"Э, {nickname or caller_name}, всё, базар окончен. Один шанс остался — говори кто ты, или вали отсюда.",
            ],
        }
        pool = fallback_pool.get(level, fallback_pool[1])
        # Choose based on call_count for more variety — different phrase each time (deterministic)
        hash_val = int(hashlib.sha256((text or "").encode()).hexdigest(), 16)
        idx = (call_count + hash_val) % len(pool)
        response_text = pool[idx]
        # Strip @mentions in fallback too — prevents bot ping-pong loops
        response_text = re.sub(r'@(\w+)', r'\1', response_text)

    # === SEND GUEST RESPONSE ===
    result = InlineQueryResultArticle(
        id=f"gop_{current_level}",
        title=f"🚬 Наехать (уровень {level})",
        description=response_text[:80] + "..." if len(response_text) > 80 else response_text,
        input_message_content=InputTextMessageContent(
            message_text=response_text + f"\n\n🚬 [{call_count+1}-й наезд] | Уровень: {level}/5" + (f" | Кличка: {nickname}" if nickname else ""),
        ),
    )

    try:
        sent = await context.bot.answer_guest_query(guest_query_id, result)
        logger.info(f"[GUEST] sent: '{response_text[:50]}...'")

        # Set cooldown to prevent bot ping-pong / flood
        if chat_id:
            db.set_cooldown(chat_id)

        # === UPDATE STATS ===
        if chat_id:
            db.increment_gopped(user.id, chat_id)
            db.increment_called(user.id, chat_id)

            # Save to history
            db.add_message(chat_id=chat_id, user_id=user.id, role="user", text=text)
            db.add_message(chat_id=chat_id, user_id=user.id, role="gop", text=response_text)

            # Escalation logic: bump level every 2 calls
            new_level = current_level
            new_count = call_count + 1
            if new_count >= 2 and current_level < 5:
                new_level = current_level + 1
            db.update_escalation(chat_id, user.id, level=new_level, message_count=new_count)

            # Check achievements
            new_achievements = ach_engine.check_all(user.id, chat_id)
            for ach in new_achievements:
                logger.info(f"[GUEST] achievement unlocked for {user.id}: {ach['title']}")

    except Exception as e:
        logger.error(f"[GUEST] failed to answer: {e}")


async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle @kolyan_byrbot mentions in groups and DMs — bot responds as itself."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text or update.message.caption or ""
    is_dm = update.effective_chat.type == "private"

    # In groups: only respond if bot is mentioned
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

    # Remove the @mention from the text to get the actual message
    bot_username = context.bot.username
    clean_text = re.sub(rf"@{bot_username}\b", "", text).strip()

    logger.info(f"[MENTION] user={user.id} ({user.first_name}), chat={chat_id}, is_dm={is_dm}, text='{clean_text[:80]}'")

    # Check blacklist
    if db.is_blacklisted(user.id, chat_id):
        return

    user_record = ensure_user(user)
    caller_name = user_record["first_name"] or user_record["username"] or "братан"

    # Get escalation state
    state = db.get_escalation(chat_id, user.id)
    current_level = state["level"] if state else 1

    # Get conversation history
    history = db.get_recent_messages(chat_id, user.id, limit=50)

    # Get nickname
    nickname = db.get_nickname(user.id, chat_id)

    # Determine context from what user said
    # If replying to someone else's message, that person is the victim
    victim_name = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        victim = update.message.reply_to_message.from_user
        if not victim.is_bot:
            victim_record = ensure_user(victim)
            victim_name = victim_record["first_name"] or victim_record["username"] or "этот"
            victim_context = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        else:
            victim_context = clean_text
    else:
        victim_context = clean_text

    # Generate response
    response_text = await llm.gop(
        victim_name=victim_name or caller_name,
        nickname=nickname if not victim_name else None,
        escalation_level=current_level,
        history=history,
        victim_context=victim_context if victim_context else None,
        caller_name=caller_name,
        self_gop=(victim_name is None),
    )

    sent = await update.message.reply_text(response_text)

    # Save messages
    db.add_message(chat_id=chat_id, user_id=user.id, role="user", text=text)
    db.add_message(chat_id=chat_id, user_id=user.id, role="gop", text=response_text)
    db.save_gop_message(sent.message_id, chat_id, user.id, current_level)

    # Update stats
    db.increment_gopped(user.id, chat_id)
    if victim_name and victim_name != caller_name:
        # Someone else was mentioned — caller called gop on victim
        # Find victim in DB if possible
        pass

    # Update escalation
    db.update_escalation(chat_id, user.id, level=current_level)

    # Check achievements
    new_achievements = ach_engine.check_all(user.id, chat_id)
    for ach in new_achievements:
        await update.message.reply_text(
            f"🏅 Ачивка разблокирована: {ach['icon']} {ach['title']} — {ach['desc']}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Init DB
    db.init_db()

    # Build app
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

    # Callbacks (style picker, etc.)
    app.add_handler(CallbackQueryHandler(handle_style_callback, pattern=r"^(style|gop_again|gop_reply|gop_stats_btn):"))

    # Guest Mode (Bot API 10.0) — @kolyan_byrbot in any chat, bot responds as itself
    # This is the modern way (2026): no need to add bot to group
    app.add_handler(MessageHandler(filters.UpdateType.GUEST_MESSAGE, handle_guest_message))

    # Mention handler — @kolyan_byrbot in groups (catches all non-command messages in groups)
    # We filter by bot username inside the handler
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND & filters.Entity("mention"),
        handle_mention
    ))
    # Also handle DMs (any non-command message in DM = talk to bot)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_mention
    ))

    # Reply handler (escalation) — must be after mention handler
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND & filters.REPLY, handle_reply))

    logger.info("🚬 Колян-бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()