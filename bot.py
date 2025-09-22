# bot.py

import os
import aiohttp
import asyncio
import sqlite3
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv
from fastapi import FastAPI, Request
import uvicorn

# --- Load environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_API_URL = os.getenv("SHEET_API_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://your-app.up.railway.app/webhook
PORT = int(os.environ.get("PORT", 5000))

# --- In-memory cache ---
cache = {}

# --- DB setup ---
conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    last_seen TEXT,
    total_requests INTEGER DEFAULT 0,
    last_report TEXT
)
""")
conn.commit()

# --- Admin ID ---
ADMIN_ID = 7679681280  # Replace with your Telegram numeric ID

# --- Helper to ignore bots ---
def is_bot(user):
    return user.is_bot

# --- Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT OR IGNORE INTO users (user_id, username, last_seen, total_requests)
        VALUES (?, ?, ?, 0)
    """, (user.id, user.username, now))
    conn.commit()
    await update.message.reply_text(
        f"👋 Hi {user.first_name}! Send your roll number (e.g., 7376221CS259) to get your student report."
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user) or user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT user_id, username, last_seen, total_requests FROM users")
    users_list = cursor.fetchall()

    if not users_list:
        await update.message.reply_text("📊 No users yet.")
        return

    user_lines = [
        f"{('@'+uname) if uname else uid} | Last seen: {last_seen} | Requests: {total_requests}"
        for uid, uname, last_seen, total_requests in users_list
    ]

    header = f"📊 Total unique users: {total_users}\n\n👥 Users:\n"
    text = header + "\n".join(user_lines)

    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user) or user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("❌ Please provide a message to broadcast.")
        return

    cursor.execute("SELECT user_id FROM users")
    all_users = cursor.fetchall()
    sent_count = 0

    for u in all_users:
        try:
            await context.bot.send_message(chat_id=u[0], text=f"📢 Broadcast:\n{msg}")
            sent_count += 1
        except:
            pass

    await update.message.reply_text(f"✅ Message sent to {sent_count} users.")

# --- Format student report ---
def format_report(data):
    lines = [
        f"Roll No : {data.get('roll','-')}",
        f"Student Name : {data.get('studentName','-')}",
        f"Course Code : {data.get('courseCode','-')}",
        f"Department : {data.get('department','-')}",
        f"Year : {data.get('year','-')}",
        f"Mentor : {data.get('mentor','-')}",
        f"Cum. Points : {data.get('cumPoints',0)}",
        f"Redeemed : {data.get('redeemed',0)}",
        f"Balance : {data.get('balance',0)}",
        f"Year Avg : {data.get('yearAvg',0)}",
        f"Status : {data.get('status','-')}"
    ]
    return "<pre>\n" + "\n".join(lines) + "\n</pre>"

async def send_report_with_buttons(update, wait_msg, data):
    keyboard = [
        [
            InlineKeyboardButton("Check another roll", callback_data="check_another"),
            InlineKeyboardButton("Contact Admin", url="https://t.me/testbitbot1")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await wait_msg.edit_text(format_report(data), parse_mode="HTML", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "check_another":
        await query.message.edit_text("📩 Send your roll number to fetch another report.")

# --- Handle roll number message ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    roll = update.message.text.strip()
    if not roll:
        await update.message.reply_text("Please send a roll number.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT OR IGNORE INTO users (user_id, username, last_seen)
        VALUES (?, ?, ?)
    """, (user.id, user.username, now))
    cursor.execute("""
        UPDATE users SET last_seen=?, total_requests = total_requests + 1
        WHERE user_id=?
    """, (now, user.id))
    conn.commit()

    wait_msg = await update.message.reply_text("⏳ Fetching your data...\n[□□□□] 0%")
    progress_steps = ["[■□□□] 25%", "[■■□□] 50%", "[■■■□] 75%", "[■■■■] 100%"]

    async def animate_progress(msg):
        for step in progress_steps:
            await msg.edit_text(f"⏳ Fetching your data...\n{step}")
            await asyncio.sleep(0.5)

    animation_task = asyncio.create_task(animate_progress(wait_msg))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SHEET_API_URL, params={"rollNo": roll}, timeout=30) as resp:
                data = await resp.json()
    except Exception as e:
        animation_task.cancel()
        await wait_msg.edit_text(f"❌ Error calling API: {e}")
        return

    animation_task.cancel()

    if not data.get("success"):
        await wait_msg.edit_text("❌ " + (data.get("error") or "Student not found."))
        return

    # Save last report in DB
    cursor.execute("""
        UPDATE users SET last_report=? WHERE user_id=?
    """, (json.dumps(data["data"]), user.id))
    conn.commit()

    cache[roll] = data["data"]
    await send_report_with_buttons(update, wait_msg, data["data"])

# --- Retrieve last report ---
async def last_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return

    cursor.execute("SELECT last_report FROM users WHERE user_id=?", (user.id,))
    result = cursor.fetchone()

    if not result or not result[0]:
        await update.message.reply_text("❌ No previous report found. Send your roll number first.")
        return

    data = json.loads(result[0])
    keyboard = [
        [
            InlineKeyboardButton("Check another roll", callback_data="check_another"),
            InlineKeyboardButton("Contact Admin", url="https://t.me/testbitbot1")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(format_report(data), parse_mode="HTML", reply_markup=reply_markup)

# --- FastAPI app ---
app = FastAPI()
app_bot: Application = ApplicationBuilder().token(BOT_TOKEN).build()

# Register handlers
app_bot.add_handler(CommandHandler("start", start))
app_bot.add_handler(CommandHandler("stats", stats))
app_bot.add_handler(CommandHandler("broadcast", broadcast))
app_bot.add_handler(CommandHandler("lastreport", last_report))
app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
app_bot.add_handler(CallbackQueryHandler(button_callback))

# --- Initialize bot and webhook ---
async def start_bot():
    await app_bot.initialize()
    await app_bot.bot.set_webhook(WEBHOOK_URL)
    print("Bot initialized and webhook set")

# --- Webhook endpoint ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app_bot.bot)
    asyncio.create_task(app_bot.process_update(update))
    return {"status": "ok"}

# --- Main entry ---
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_bot())
    uvicorn.run(app, host="0.0.0.0", port=PORT)
