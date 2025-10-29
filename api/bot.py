# api/bot.py

import os
import aiohttp
import asyncio
import aiomysql
import json
from fastapi import FastAPI, Request
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from mangum import Mangum

# --- Load environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_API_URL = os.getenv("SHEET_API_URL")
# webhook URL used to register with Telegram (Vercel route)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bit-reward-point-checker-bot.vercel.app/api/webhook")
PORT = int(os.environ.get("PORT", 5000))

# --- MySQL config ---
MYSQLHOST = os.getenv("MYSQLHOST")
MYSQLPORT = int(os.getenv("MYSQLPORT", 3306))
MYSQLUSER = os.getenv("MYSQLUSER")
MYSQLPASSWORD = os.getenv("MYSQLPASSWORD")
MYSQLDATABASE = os.getenv("MYSQLDATABASE")

# --- Admin ID ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "7679681280"))                             

# --- MySQL pool ---
pool = None

# --- Initialize DB safely ---
async def init_db():
    global pool
    pool = await aiomysql.create_pool(
        host=MYSQLHOST,
        port=MYSQLPORT,
        user=MYSQLUSER,
        password=MYSQLPASSWORD,
        db=MYSQLDATABASE,
        autocommit=True,
        maxsize=10
    )
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    last_seen DATETIME,
                    total_requests INT DEFAULT 0,
                    last_report JSON
                )
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    roll_no VARCHAR(50),
                    report JSON,
                    created_at DATETIME
                )
            """)

# --- Helper to ignore bots ---
def is_bot(user):
    return getattr(user, "is_bot", False)

# --- Telegram Bot ---
app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

# ======================== TELEGRAM HANDLERS ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT IGNORE INTO users (user_id, username, last_seen, total_requests)
                VALUES (%s, %s, %s, 0)
            """, (user.id, user.username, now))
    await update.message.reply_text(
        f"👋 Hi {user.first_name}! Send your roll number (e.g., 7376221CS259) to get your student report."
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user) or user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM users")
            total_users = (await cur.fetchone())[0]
            await cur.execute("SELECT user_id, username, last_seen, total_requests FROM users")
            users_list = await cur.fetchall()
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
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM users")
            all_users = await cur.fetchall()
    sent_count = 0
    for u in all_users:
        try:
            await context.bot.send_message(chat_id=u[0], text=f"📢 Broadcast:\n{msg}")
            sent_count += 1
        except:
            pass
    await update.message.reply_text(f"✅ Message sent to {sent_count} users.")

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    roll = update.message.text.strip()
    if not roll:
        await update.message.reply_text("Please send a roll number.")
        return

    wait_msg = await update.message.reply_text("⏳ Fetching your data...")

    async def animate_timer(msg):
        symbols = ["⏳", "⌛"]
        i = 0
        while True:
            try:
                await msg.edit_text(f"{symbols[i%2]} Fetching your data...")
                i += 1
                await asyncio.sleep(0.5)
            except:
                break

    animation_task = asyncio.create_task(animate_timer(wait_msg))

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

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO users (user_id, username, last_seen, total_requests, last_report)
                VALUES (%s, %s, %s, 1, %s)
                ON DUPLICATE KEY UPDATE last_seen=%s, total_requests=total_requests+1, last_report=%s
            """, (user.id, user.username, now, json.dumps(data["data"]), now, json.dumps(data["data"])))
            await cur.execute("""
                INSERT INTO reports (user_id, roll_no, report, created_at)
                VALUES (%s, %s, %s, %s)
            """, (user.id, roll, json.dumps(data["data"]), now))

    await send_report_with_buttons(update, wait_msg, data["data"])

async def last_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT last_report FROM users WHERE user_id=%s", (user.id,))
            result = await cur.fetchone()
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

# Register handlers
app_bot.add_handler(CommandHandler("start", start))
app_bot.add_handler(CommandHandler("stats", stats))
app_bot.add_handler(CommandHandler("broadcast", broadcast))
app_bot.add_handler(CommandHandler("lastreport", last_report))
app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
app_bot.add_handler(CallbackQueryHandler(button_callback))

# ======================== FASTAPI app with lifespan ========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # initialize DB pool and set webhook
    await init_db()
    await app_bot.initialize()
    # set webhook on telegram to the Vercel route
    await app_bot.bot.set_webhook(WEBHOOK_URL)
    print("Bot initialized and webhook set ✅")
    yield
    pool.close()
    await pool.wait_closed()

app = FastAPI(lifespan=lifespan)

# root route for quick test
@app.get("/")
async def home():
    return {"status": "running", "message": "Vercel FastAPI Telegram Bot ✅"}

# webhook endpoint
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app_bot.bot)
    asyncio.create_task(app_bot.process_update(update))
    return {"status": "ok"}

# Serverless handler required by Vercel
handler = Mangum(app)

# local dev support
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
