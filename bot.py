# ================================
# TELEGRAM BOT: АНАЛИЗ ЧАТА (НОВАЯ ВЕРСИЯ)
# ================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from collections import defaultdict
from datetime import datetime, timedelta

TOKEN = "8112024839:AAGCNNqoYGKAp87lw0hvnhlwIIbKB3dLZRc"

# Хранилища
message_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
message_texts = defaultdict(lambda: defaultdict(list))
user_names = {}


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


# ───────────── ОБРАБОТКА КНОПОК ─────────────
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


# ───────────── СТАТИСТИКА СЕГОДНЯ ─────────────
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


# ───────────── ОБРАБОТКА ТЕКСТА ─────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.is_bot:
        return

    text = update.message.text

    # 🚫 Не считаем команды как текстовые сообщения
    if text.startswith("/"):
        return

    chat_id = update.message.chat_id
    user = update.message.from_user
    date_str = update.message.date.strftime("%Y-%m-%d")
    display_date = update.message.date.strftime("%d-%m-%Y")

    # сохраняем сообщение
    user_names[user.id] = user.full_name
    message_stats[chat_id][date_str][user.id] += 1
    message_texts[chat_id][date_str].append((user.id, text.lower()))

    state = context.user_data.get("await")

    # ── ПЕРИОД ──
    if state == "period":
        try:
            start_d, end_d = text.split()
            start = datetime.strptime(start_d, "%d-%m-%Y")
            end = datetime.strptime(end_d, "%d-%m-%Y")
        except:
            await update.message.reply_text("❌ Неверный формат.\nПример: 01-12-2025 10-12-2025")
            return

        result = defaultdict(int)
        cur = start
        while cur <= end:
            day_key = cur.strftime("%Y-%m-%d")
            for uid, cnt in message_stats.get(chat_id, {}).get(day_key, {}).items():
                result[uid] += cnt
            cur += timedelta(days=1)

        if not result:
            await update.message.reply_text("Нет данных за этот период")
        else:
            lines = [f"📆 Статистика с {start_d} по {end_d}:\n"]
            for uid, cnt in sorted(result.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")
            await update.message.reply_text("\n".join(lines))

        context.user_data.clear()
        await update.message.reply_text("Выберите действие:", reply_markup=menu_keyboard())
        return

    # ── ПОИСК ──
    elif state == "search":
        query = text.lower()
        total = defaultdict(int)

        for day_data in message_texts.get(chat_id, {}).values():
            for uid, msg in day_data:
                if query in msg:
                    total[uid] += 1

        if not total:
            await update.message.reply_text(f"Совпадений с '{query}' не найдено")
        else:
            icon = "🔍" if context.user_data.get("mode") == "word" else "#️⃣"
            lines = [f"{icon} Найдено сообщений с '{query}':\n"]
            for uid, cnt in sorted(total.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"{user_names.get(uid, 'Неизвестный')}: {cnt}")
            await update.message.reply_text("\n".join(lines))

        context.user_data.clear()
        await update.message.reply_text("Выберите действие:", reply_markup=menu_keyboard())


# ───────────── ЗАПУСК ─────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot is running...")
    app.run_polling()
