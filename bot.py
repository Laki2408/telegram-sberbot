# ================================
# TELEGRAM BOT: CHAT ANALYTICS (MULTI-CHAT, ADMIN DM)
# ================================

import os
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMemberAdministrator,
    ChatMemberOwner,
)
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ───────────── ENV ─────────────
TOKEN = os.getenv("TOKEN")
DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")

if not TOKEN or not DOMAIN:
    raise RuntimeError("TOKEN or KOYEB_PUBLIC_DOMAIN is missing")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://{DOMAIN}{WEBHOOK_PATH}"

# ───────────── ХРАНИЛИЩА ─────────────
message_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
message_texts = defaultdict(lambda: defaultdict(list))
user_names = {}
known_chats = {}  # chat_id -> title

# ───────────── FASTAPI ─────────────
app = FastAPI()
telegram_app: Application = ApplicationBuilder().token(TOKEN).build()

# ───────────── HELPERS ─────────────
async def is_admin(bot, chat_id, user_id) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))

def chat_menu(chat_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ℹ️ Информация о чате", callback_data=f"info:{chat_id}")],
        [InlineKeyboardButton("📊 Сегодня", callback_data=f"today:{chat_id}")],
        [InlineKeyboardButton("📆 Статистика за период", callback_data=f"period:{chat_id}")],
        [InlineKeyboardButton("🔍 Поиск по слову", callback_data=f"search_word:{chat_id}")],
        [InlineKeyboardButton("#️⃣ Поиск по хештегу", callback_data=f"search_tag:{chat_id}")],
        [InlineKeyboardButton("🔄 Сменить чат", callback_data="change_chat")],
    ])

def chat_select_keyboard(user_id, bot):
    buttons = []
    for cid, title in known_chats.items():
        buttons.append([InlineKeyboardButton(title, callback_data=f"select:{cid}")])
    return InlineKeyboardMarkup(buttons)

# ───────────── /start (ТОЛЬКО ЛС) ─────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    context.user_data.clear()
    await update.message.reply_text(
        "Выберите чат для управления:",
        reply_markup=chat_select_keyboard(update.effective_user.id, context.bot)
    )

# ───────────── CALLBACK ─────────────
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "change_chat":
        context.user_data.clear()
        await query.message.reply_text(
            "Выберите чат для управления:",
            reply_markup=chat_select_keyboard(query.from_user.id, context.bot)
        )
        return

    action, chat_id = query.data.split(":")
    chat_id = int(chat_id)
    context.user_data["chat_id"] = chat_id

    if action == "select":
        await query.message.reply_text(
            f"Управление чатом: {known_chats.get(chat_id)}",
            reply_markup=chat_menu(chat_id)
        )

    elif action == "info":
        stats = message_stats.get(chat_id, {})
        users = set()
        total = 0
        for day in stats.values():
            for uid, cnt in day.items():
                users.add(uid)
                total += cnt

        await query.message.reply_text(
            f"ℹ️ Чат: {known_chats.get(chat_id)}\n"
            f"👥 Активных участников: {len(users)}\n"
            f"💬 Сообщений всего: {total}"
        )

    elif action == "today":
        today = datetime.utcnow().strftime("%d-%m-%Y")
        stats = message_stats.get(chat_id, {}).get(today, {})

        if not stats:
            await query.message.reply_text("Сегодня сообщений нет")
            return

        lines = ["📊 Сегодня:\n"]
        for uid, cnt in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")

        await query.message.reply_text("\n".join(lines))

    elif action in ("period", "search_word", "search_tag"):
        context.user_data["await"] = action
        await query.message.reply_text("Введите период: ДД-ММ-ГГГГ ДД-ММ-ГГГГ")

# ───────────── СБОР СООБЩЕНИЙ (ГРУППЫ) ─────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.is_bot:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ("group", "supergroup"):
        known_chats[chat.id] = chat.title
        date_str = update.message.date.strftime("%d-%m-%Y")

        user_names[user.id] = user.full_name
        message_stats[chat.id][date_str][user.id] += 1
        message_texts[chat.id][date_str].append((user.id, update.message.text.lower()))

# ───────────── WEBHOOK ─────────────
@app.on_event("startup")
async def startup():
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(menu_callback))
    telegram_app.add_handler(MessageHandler(filters.TEXT, handle_text))

    await telegram_app.initialize()

    try:
        await telegram_app.bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        print("Webhook set:", WEBHOOK_URL)
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
