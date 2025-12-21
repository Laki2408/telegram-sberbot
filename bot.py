# ================================
# TELEGRAM BOT: АНАЛИЗ ЧАТА (KOYEB WEBHOOK)
# ================================

import os
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
PORT = int(os.getenv("PORT", 8000))
DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")

if not TOKEN or not DOMAIN:
    raise RuntimeError("TOKEN or KOYEB_PUBLIC_DOMAIN is missing")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://{DOMAIN}{WEBHOOK_PATH}"

# ───────────── ХРАНИЛИЩА ─────────────
message_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
message_texts = defaultdict(lambda: defaultdict(list))
user_names = {}

# ───────────── FASTAPI ─────────────
app = FastAPI()
telegram_app: Application = ApplicationBuilder().token(TOKEN).build()

# ───────────── МЕНЮ ─────────────
def menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика за сегодня", callback_data="today")],
        [InlineKeyboardButton("📆 Статистика за период", callback_data="period")],
        [InlineKeyboardButton("🔍 Поиск по слову", callback_data="search_word")],
        [InlineKeyboardButton("#️⃣ Поиск по хештегу", callback_data="search_tag")],
    ])

# ───────────── СТАРТ ─────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Выберите действие:", reply_markup=menu_keyboard())

# ───────────── КНОПКИ ─────────────
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    if query.data == "today":
        await show_today(query)
        await query.message.reply_text("Выберите действие:", reply_markup=menu_keyboard())

    elif query.data == "period":
        context.user_data["await"] = "period"
        await query.message.reply_text("Введите период: ДД-ММ-ГГГГ ДД-ММ-ГГГГ")

    elif query.data == "search_word":
        context.user_data["await"] = "search"
        context.user_data["mode"] = "word"
        await query.message.reply_text("Введите слово для поиска")

    elif query.data == "search_tag":
        context.user_data["await"] = "search"
        context.user_data["mode"] = "tag"
        await query.message.reply_text("Введите хештег")

# ───────────── СЕГОДНЯ ─────────────
async def show_today(query):
    chat_id = query.message.chat_id
    today = datetime.utcnow().strftime("%Y-%m-%d")

    stats = message_stats.get(chat_id, {}).get(today)
    if not stats:
        await query.message.reply_text("Сегодня сообщений ещё нет")
        return

    lines = ["📊 Сообщения за сегодня:\n"]
    for uid, cnt in sorted(stats.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")

    await query.message.reply_text("\n".join(lines))

# ───────────── ТЕКСТ ─────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.is_bot:
        return

    text = update.message.text
    if text.startswith("/"):
        return

    chat_id = update.message.chat_id
    user = update.message.from_user
    date_str = update.message.date.strftime("%Y-%m-%d")

    user_names[user.id] = user.full_name
    message_stats[chat_id][date_str][user.id] += 1
    message_texts[chat_id][date_str].append((user.id, text.lower()))

    state = context.user_data.get("await")

    if state == "period":
        try:
            start_d, end_d = text.split()
            start = datetime.strptime(start_d, "%d-%m-%Y")
            end = datetime.strptime(end_d, "%d-%m-%Y")
        except:
            await update.message.reply_text("❌ Формат: 01-12-2025 10-12-2025")
            return

        result = defaultdict(int)
        cur = start
        while cur <= end:
            key = cur.strftime("%Y-%m-%d")
            for uid, cnt in message_stats.get(chat_id, {}).get(key, {}).items():
                result[uid] += cnt
            cur += timedelta(days=1)

        if not result:
            await update.message.reply_text("Нет данных за период")
        else:
            lines = [f"📆 Статистика с {start_d} по {end_d}:\n"]
            for uid, cnt in sorted(result.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")
            await update.message.reply_text("\n".join(lines))

        context.user_data.clear()
        await update.message.reply_text("Выберите действие:", reply_markup=menu_keyboard())

    elif state == "search":
        q = text.lower()
        total = defaultdict(int)

        for day in message_texts.get(chat_id, {}).values():
            for uid, msg in day:
                if q in msg:
                    total[uid] += 1

        if not total:
            await update.message.reply_text(f"Совпадений с '{q}' не найдено")
        else:
            icon = "🔍" if context.user_data.get("mode") == "word" else "#️⃣"
            lines = [f"{icon} Найдено сообщений с '{q}':\n"]
            for uid, cnt in sorted(total.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")
            await update.message.reply_text("\n".join(lines))

        context.user_data.clear()
        await update.message.reply_text("Выберите действие:", reply_markup=menu_keyboard())

# ───────────── WEBHOOK ─────────────
@app.on_event("startup")
async def startup():
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(menu_callback))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    print("Webhook set:", WEBHOOK_URL)

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
