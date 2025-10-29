# api/bot.py

import os
import aiohttp
import asyncio
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

# local imports
from api.models import upsert_user, create_user_if_missing, save_report, get_last_report

# --- Load environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_API_URL = os.getenv("SHEET_API_URL", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bit-reward-point-checker-bot.vercel.app/api/webhook")   
PORT = int(os.environ.get("PORT", 5000))
ADMIN_ID = int(os.getenv("ADMIN_ID", "7679681280"))

if not BOT_TOKEN:
    # Fail gracefully in serverless logs — don't crash deployment immediately
    print("WARNING: BOT_TOKEN is not set. Bot will not initialize properly until BOT_TOKEN is provided.")

# --- Telegram Bot ---
app_bot = None
APP_BOT_INITIALIZED = False
if BOT_TOKEN:
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

# --- Helper to ignore bots ---
def is_bot(user):
    return getattr(user, "is_bot", False)

# ======================== TELEGRAM HANDLERS ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    now = datetime.utcnow()
    await create_user_if_missing(user.id, user.username, now)
    try:
        await update.message.reply_text(
            f"👋 Hi {user.first_name}! Send your roll number (e.g., 7376221CS259) to get your student report."
        )
    except Exception:
        pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user) or user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return
    # count users
    try:
        from api.db import get_collection
        users_col = get_collection("users")
        total_users = await users_col.count_documents({})
        await update.message.reply_text(f"📊 Total users: {total_users}")
    except Exception as e:
        await update.message.reply_text("⚠️ DB not configured. Set MONGO_URI to enable stats.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user) or user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("❌ Please provide a message to broadcast.")
        return
    try:
        from api.db import get_collection
        users_col = get_collection("users")
        cursor = users_col.find({}, {"user_id": 1})
    except Exception:
        await update.message.reply_text("⚠️ DB not configured. Set MONGO_URI to enable broadcast.")
        return
    sent_count = 0
    async for doc in cursor:
        try:
            await context.bot.send_message(chat_id=doc["user_id"], text=f"📢 Broadcast:\n{msg}")
            sent_count += 1
        except Exception:
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
        try:
            await update.message.reply_text("Please send a roll number.")
        except Exception:
            pass
        return

    try:
        wait_msg = await update.message.reply_text("⏳ Fetching your data...")
    except Exception:
        # if sending message fails due to transient transport close, continue processing anyway
        wait_msg = update.message

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SHEET_API_URL, params={"rollNo": roll}, timeout=8) as resp:
                data = await resp.json()
    except Exception as e:
        try:
            await wait_msg.edit_text(f"❌ Error calling API: {e}")
        except Exception:
            pass
        return

    if not data.get("success"):
        try:
            await wait_msg.edit_text("❌ " + (data.get("error") or "Student not found."))
        except Exception:
            pass
        return

    now = datetime.utcnow()
    # save report in mongo
    try:
        await save_report(user.id, roll, data["data"])
    except Exception as e:
        try:
            await wait_msg.edit_text(f"❌ DB error: {e}")
        except Exception:
            pass
        return

    await send_report_with_buttons(update, wait_msg, data["data"])

async def last_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    data = await get_last_report(user.id)
    if not data:
        await update.message.reply_text("❌ No previous report found. Send your roll number first.")
        return
    keyboard = [
        [
            InlineKeyboardButton("Check another roll", callback_data="check_another"),
            InlineKeyboardButton("Contact Admin", url="https://t.me/testbitbot1")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(format_report(data), parse_mode="HTML", reply_markup=reply_markup)

# Register handlers only if bot is initialized
if app_bot is not None:
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("stats", stats))
    app_bot.add_handler(CommandHandler("broadcast", broadcast))
    app_bot.add_handler(CommandHandler("lastreport", last_report))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    # swallow errors so serverless loop shutdown doesn't bubble up
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
        try:
            err = getattr(context, "error", None)
            print("Telegram handler error:", err)
        except Exception:
            pass
    app_bot.add_error_handler(on_error)

# ======================== FASTAPI app with lifespan ========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # do not crash if MONGO not present — raise clear message instead
    from api.db import get_db
    try:
        _ = get_db()  # verifies connection
    except Exception as e:
        print("DB connection failed at startup:", e)
        # allow function to still boot (so we can see logs), but return early
    # initialize telegram bot and set webhook only if token present
    try:
        global APP_BOT_INITIALIZED
        if app_bot is not None and not APP_BOT_INITIALIZED:
            await app_bot.initialize()
            APP_BOT_INITIALIZED = True
        if BOT_TOKEN and WEBHOOK_URL and app_bot is not None:
            await app_bot.bot.set_webhook(WEBHOOK_URL)
            print("Bot webhook set to", WEBHOOK_URL)
        else:
            print("Skipping set_webhook because BOT_TOKEN or WEBHOOK_URL missing")
    except Exception as e:
        print("Telegram init error:", e)
    yield
    # no DB pool to close (motor handles connections), but we include cleanups if necessary

app = FastAPI(lifespan=lifespan)

# root route for quick test
@app.get("/")
async def home():
    return {"status": "running", "message": "Vercel FastAPI Telegram Bot ✅"}

# webhook endpoint
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    if app_bot is None:
        return {"status": "error", "message": "BOT_TOKEN not configured"}, 500
    # Ensure lazy initialization in case startup lifecycle didn't run yet in serverless
    global APP_BOT_INITIALIZED
    if not APP_BOT_INITIALIZED:
        try:
            await app_bot.initialize()
            APP_BOT_INITIALIZED = True
        except Exception as e:
            return {"status": "error", "message": f"Bot init failed: {e}"}, 500
    try:
        data = await request.json()
    except Exception:
        return {"status": "bad request"}, 400
    update = Update.de_json(data, app_bot.bot)
    # Await processing to keep the event loop alive in serverless
    await app_bot.process_update(update)
    return {"status": "ok"}

# convenience GET to verify webhook URL in a browser
@app.get("/api/webhook")
async def webhook_info():
    return {"status": "ok", "message": "Send POST requests from Telegram to this endpoint"}

# Vercel detects ASGI apps by the exported `app` variable; no extra handler needed.

# local dev support
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
