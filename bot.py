import os
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberAdministrator, ChatMemberOwner
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ChatMemberHandler,
)

TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", 8000))
DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")

if not TOKEN or not DOMAIN:
    raise RuntimeError("TOKEN or KOYEB_PUBLIC_DOMAIN is missing")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://{DOMAIN}{WEBHOOK_PATH}"

message_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
message_texts = defaultdict(lambda: defaultdict(list))
user_names = {}
known_chats = {}

app = FastAPI()
telegram_app: Application = ApplicationBuilder().token(TOKEN).build()

def chat_menu(chat_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ℹ️ Информация о чате", callback_data=f"info:{chat_id}")],
        [InlineKeyboardButton("📊 Сегодня", callback_data=f"today:{chat_id}")],
        [InlineKeyboardButton("📆 Выбрать период", callback_data=f"set_period:{chat_id}")],
        [InlineKeyboardButton("📝 Кол-во слов (все)", callback_data=f"words_all:{chat_id}")],
        [InlineKeyboardButton("🔍 Кол-во слов (по слову)", callback_data=f"words_word:{chat_id}")],
        [InlineKeyboardButton("#️⃣ Кол-во слов (по хештегу)", callback_data=f"words_tag:{chat_id}")],
        [InlineKeyboardButton("🔄 Сменить чат", callback_data="change_chat")],
    ])

def chat_select_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(title, callback_data=f"select:{cid}")]
        for cid, title in known_chats.items()
    ])

def normalize(word: str) -> str:
    return word.strip(".,!?()[]{}:;\"'").lower()

async def is_admin(bot, chat_id, user_id) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if not known_chats:
        await update.message.reply_text("Я ещё не добавлен ни в один чат.")
        return
    context.user_data.clear()
    await update.message.reply_text("Выберите чат:", reply_markup=chat_select_keyboard())

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "change_chat":
        context.user_data.clear()
        await query.message.reply_text("Выберите чат:", reply_markup=chat_select_keyboard())
        return
    action, chat_id = query.data.split(":")
    chat_id = int(chat_id)
    context.user_data["chat_id"] = chat_id
    if not await is_admin(context.bot, chat_id, query.from_user.id):
        await query.message.reply_text("⛔ Только для администраторов")
        return
    if action == "select":
        await query.message.reply_text(f"Управление чатом: {known_chats.get(chat_id)}", reply_markup=chat_menu(chat_id))
    elif action == "info":
        stats = message_stats.get(chat_id, {})
        users = set()
        total = 0
        for day in stats.values():
            for uid, cnt in day.items():
                users.add(uid)
                total += cnt
        await query.message.reply_text(f"ℹ️ Чат: {known_chats.get(chat_id)}\n👥 Активных участников: {len(users)}\n💬 Сообщений всего: {total}")
    elif action == "today":
        chat_stats = message_stats.get(chat_id, {})
        today_date = datetime.utcnow().date()
        stats = {}
        for date_str, users in chat_stats.items():
            msg_date = datetime.strptime(date_str, "%d-%m-%Y").date()
            if msg_date == today_date:
                stats = users
                break
        if not stats:
            await query.message.reply_text("Сегодня сообщений нет")
            return
        lines = ["📊 Сегодня:\n"]
        for uid, cnt in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")
        await query.message.reply_text("\n".join(lines))
    elif action in ("set_period", "words_all", "words_word", "words_tag"):
        context.user_data["mode"] = "words_all" if action == "set_period" else action
        context.user_data["step"] = "period"
        await query.message.reply_text("Введите период:\nДД-ММ-ГГГГ ДД-ММ-ГГГГ")

async def input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    chat_id = context.user_data.get("chat_id")
    mode = context.user_data.get("mode")
    step = context.user_data.get("step")
    if not chat_id or not mode or not step:
        return
    text = update.message.text.strip()
    if step == "period":
        try:
            start_str, end_str = text.split()
        except:
            await update.message.reply_text("❌ Неверный формат")
            return
        context.user_data["period"] = (start_str, end_str)
        if mode == "words_all":
            await show_word_stats(update, chat_id, start_str, end_str)
            context.user_data.clear()
            return
        context.user_data["step"] = "value"
        await update.message.reply_text("Введите слово или хештег")
    elif step == "value":
        start_str, end_str = context.user_data["period"]
        value = normalize(text)
        if mode == "words_word":
            await show_word_stats(update, chat_id, start_str, end_str, word=value)
        elif mode == "words_tag":
            if not value.startswith("#"):
                await update.message.reply_text("❌ Хештег должен начинаться с #")
                return
            await show_word_stats(update, chat_id, start_str, end_str, tag=value)
        context.user_data.clear()

async def show_word_stats(update, chat_id, start_str, end_str, word=None, tag=None):
    counter = defaultdict(int)
    total = 0
    for date_str, msgs in message_texts.get(chat_id, {}).items():
        if not (start_str <= date_str <= end_str):
            continue
        for uid, text in msgs:
            for w in text.split():
                w_norm = normalize(w)
                if word and w_norm != word:
                    continue
                if tag and w_norm != tag:
                    continue
                counter[uid] += 1
                total += 1
    if not counter:
        await update.message.reply_text("Данных нет")
        return
    lines = ["📝 Статистика слов:\n"]
    for uid, cnt in sorted(counter.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")
    lines.append(f"\n📊 Всего слов: {total}")
    await update.message.reply_text("\n".join(lines))

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
        if update.message.text:
            message_texts[chat.id][date_str].append((user.id, update.message.text.lower()))

async def track_new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        known_chats[chat.id] = chat.title

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(menu_callback))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, input_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT, handle_text))
telegram_app.add_handler(ChatMemberHandler(track_new_chat, ChatMemberHandler.MY_CHAT_MEMBER))

@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    try:
        current = await telegram_app.bot.get_webhook_info()
        if current.url != WEBHOOK_URL:
            await telegram_app.bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        print("Webhook set:", WEBHOOK_URL)
    except RetryAfter as e:
        print(f"Webhook flood control, retry after {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
    except Exception as e:
        print(f"Error in startup: {e}")

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "ok"}
