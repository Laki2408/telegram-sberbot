import os
from collections import defaultdict
from datetime import datetime

from fastapi import FastAPI, Request
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatMemberAdministrator,
    ChatMemberOwner,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================================
# CONFIG
# ================================
TOKEN = os.getenv("TOKEN")
DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")
PORT = int(os.getenv("PORT", 8000))

if not TOKEN or not DOMAIN:
    raise RuntimeError("TOKEN or KOYEB_PUBLIC_DOMAIN is missing")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://{DOMAIN}{WEBHOOK_PATH}"

# ================================
# FASTAPI APP (ASGI)
# ================================
app = FastAPI()

# ================================
# STORAGE (RAM)
# ================================
message_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
message_texts = defaultdict(lambda: defaultdict(list))
user_names = {}
known_chats = {}

# ================================
# HELPERS
# ================================
async def is_admin(bot, chat_id, user_id) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))


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


def parse_period(text: str):
    start, end = text.split()
    return (
        datetime.strptime(start, "%d-%m-%Y"),
        datetime.strptime(end, "%d-%m-%Y"),
    )


def normalize(word: str) -> str:
    return word.strip(".,!?()[]{}:;\"'").lower()

# ================================
# BOT HANDLERS
# ================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    # Logging to check if the bot has added any chats
    if not known_chats:
        await update.message.reply_text("Я ещё не добавлен ни в один чат.")
        print("No chats found. Bot hasn't been added to any chats yet.")
        return

    context.user_data.clear()
    await update.message.reply_text(
        "Выберите чат:",
        reply_markup=chat_select_keyboard()
    )


async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    valid_chats = {}
    for chat_id, title in known_chats.items():
        try:
            await context.bot.get_chat(chat_id)
            valid_chats[chat_id] = title
        except Exception as e:
            print(f"Error checking chat {chat_id}: {e}")

    known_chats.clear()
    known_chats.update(valid_chats)

    if not known_chats:
        await update.message.reply_text(
            "❗ Напиши любое сообщение в группе, где есть бот, чтобы обновить список чатов."
        )
        return

    await update.message.reply_text(
        "Список чатов обновлён",
        reply_markup=chat_select_keyboard()
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "change_chat":
        context.user_data.clear()
        await query.message.reply_text(
            "Выберите чат:",
            reply_markup=chat_select_keyboard()
        )
        return

    action, chat_id = query.data.split(":")
    chat_id = int(chat_id)
    context.user_data["chat_id"] = chat_id

    if not await is_admin(context.bot, chat_id, query.from_user.id):
        await query.message.reply_text("⛔ Только для администраторов")
        return

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
            lines.append(f"{user_names.get(uid)}: {cnt}")
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
            start, end = parse_period(text)
        except:
            await update.message.reply_text("❌ Неверный формат")
            return

        context.user_data["period"] = (start, end)

        if mode == "words_all":
            await show_word_stats(update, chat_id, start, end)
            context.user_data.clear()
            return

        context.user_data["step"] = "value"
        await update.message.reply_text("Введите слово или хештег")

    elif step == "value":
        start, end = context.user_data["period"]
        value = normalize(text)

        if mode == "words_word":
            await show_word_stats(update, chat_id, start, end, word=value)
        elif mode == "words_tag":
            if not value.startswith("#"):
                await update.message.reply_text("❌ Хештег должен начинаться с #")
                return
            await show_word_stats(update, chat_id, start, end, tag=value)

        context.user_data.clear()


async def show_word_stats(update, chat_id, start, end, word=None, tag=None):
    counter = defaultdict(int)
    total = 0

    for date_str, msgs in message_texts.get(chat_id, {}).items():
        date = datetime.strptime(date_str, "%d-%m-%Y")
        if not (start <= date <= end):
            continue

        for uid, text in msgs:
            for raw in text.split():
                w = normalize(raw)
                if word and w != word:
                    continue
                if tag and w != tag:
                    continue
                counter[uid] += 1
                total += 1

    if not counter:
        await update.message.reply_text("Данных нет")
        return

    lines = ["📝 Статистика слов:\n"]
    for uid, cnt in sorted(counter.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"{user_names.get(uid)}: {cnt}")
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
            message_texts[chat.id][date_str].append(
                (user.id, update.message.text.lower())
            )



