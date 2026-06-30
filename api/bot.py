# api/bot.py

import os
import aiohttp
import asyncio
import json
import csv
import io
from fastapi import FastAPI, Request
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🔍 Check Points"), KeyboardButton("🕒 My Last Report")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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
    
    # Check if user has a saved roll number in DB
    try:
        from api.db import get_collection
        import asyncio
        users_col = get_collection("users")
        doc = await asyncio.to_thread(users_col.find_one, {"user_id": int(user.id)})
        last_roll = doc.get("last_roll") if doc else None
    except Exception:
        last_roll = None
        
    reply_markup = get_main_keyboard()
    
    if last_roll:
        inline_kb = [
            [
                InlineKeyboardButton(f"📊 Check {last_roll}", callback_data=f"check_saved_{last_roll}"),
                InlineKeyboardButton("✏️ Different Roll", callback_data="check_another")
            ]
        ]
        inline_markup = InlineKeyboardMarkup(inline_kb)
        try:
            await update.message.reply_html(
                f"👋 <b>Welcome back, {user.first_name}!</b>\n\n"
                f"I found your saved Roll Number: <code>{last_roll}</code>\n\n"
                f"Would you like to check your points for this roll number?",
                reply_markup=reply_markup
            )
            await update.message.reply_html(
                "👇 Choose an action below:",
                reply_markup=inline_markup
            )
        except Exception:
            pass
    else:
        try:
            await update.message.reply_html(
                f"👋 <b>Welcome to BIT Reward Point Checker, {user.first_name}!</b>\n\n"
                f"Keep track of your reward points, redemptions, and current balance easily.\n\n"
                f"📝 <i>Please send your Roll Number (e.g. <code>7376221CS259</code>) to fetch your report.</i>",
                reply_markup=reply_markup
            )
        except Exception:
            pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user) or user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return
    # count users and find most recent
    try:
        from api.db import get_collection
        import asyncio
        users_col = get_collection("users")
        total_users = await asyncio.to_thread(users_col.count_documents, {})
        
        recent_user = await asyncio.to_thread(
            lambda: users_col.find_one(sort=[("last_seen", -1)])
        )
        
        msg_text = f"📊 <b>Total users</b>: {total_users}"
        if recent_user:
            username = recent_user.get("username")
            username_str = f"@{username}" if username else "No username"
            last_seen = recent_user.get("last_seen")
            last_seen_str = last_seen.strftime("%Y-%m-%d %H:%M:%S UTC") if last_seen else "Unknown"
            total_reqs = recent_user.get("total_requests", 0)
            
            last_report = recent_user.get("last_report")
            report_str = "None"
            if last_report:
                roll = last_report.get("roll", "-")
                name = last_report.get("studentName", "-")
                report_str = f"Roll: <code>{roll}</code> ({name})"
                
            msg_text += (
                f"\n\n👤 <b>Most Recent User</b>:\n"
                f"• <b>ID</b>: <code>{recent_user.get('user_id')}</code>\n"
                f"• <b>Username</b>: {username_str}\n"
                f"• <b>Last Seen</b>: {last_seen_str}\n"
                f"• <b>Total Requests</b>: {total_reqs}\n"
                f"• <b>Last Checked</b>: {report_str}"
            )
        await update.message.reply_html(msg_text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ DB error: {e}. Check MONGO_URI and network access.")

async def export_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user) or user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return
    try:
        from api.db import get_collection
        import asyncio
        import io
        import csv
        
        users_col = get_collection("users")
        users = await asyncio.to_thread(lambda: list(users_col.find({})))
        
        # Build CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Username", "Last Seen", "Total Requests", "Last Roll", "Last Name", "Last Balance"])
        
        for u in users:
            last_rep = u.get("last_report") or {}
            writer.writerow([
                u.get("user_id"),
                u.get("username") or "",
                u.get("last_seen"),
                u.get("total_requests", 0),
                last_rep.get("roll") or "",
                last_rep.get("studentName") or "",
                last_rep.get("balance") or 0
            ])
            
        output.seek(0)
        # Send as document
        bio = io.BytesIO(output.getvalue().encode('utf-8'))
        bio.name = "users_report.csv"
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=bio,
            filename="users_report.csv",
            caption=f"📊 Current User Database Report ({len(users)} users)"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Export failed: {e}")

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
        import asyncio
        users_col = get_collection("users")
        # fetch all user ids synchronously in a separate thread
        users = await asyncio.to_thread(lambda: list(users_col.find({}, {"user_id": 1})))
    except Exception:
        await update.message.reply_text("⚠️ DB error. Set MONGO_URI and ensure access to enable broadcast.")
        return
    sent_count = 0
    for doc in users:
        try:
            await context.bot.send_message(chat_id=doc["user_id"], text=f"📢 Broadcast:\n{msg}")
            sent_count += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Message sent to {sent_count} users.")

# Fallback/default dates in case sheet fetching fails
DEFAULT_REDEMPTION_DATES = {
    "S7": {"ip1": "29.08.2026", "ip2": "17.10.2026"},
    "S5": {"ip1": "29.08.2026", "ip2": "17.10.2026"},
    "S3": {"ip1": "31.08.2026", "ip2": "23.10.2026"},
    "S1": {"ip1": "Not scheduled (-)", "ip2": "Not scheduled (-)"}
}

# Cache structure
_dates_cache = None
_cache_expiry = None
CACHE_DURATION = timedelta(minutes=10)

DETAILS_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1w6OQ5E0Gus-3eaSErrB3TSBof2MxBwkkHz4X5Hcx-2w/export?format=csv&gid=409527497"

async def fetch_live_redemption_dates():
    global _dates_cache, _cache_expiry
    now = datetime.utcnow()
    
    if _dates_cache and _cache_expiry and now < _cache_expiry:
        return _dates_cache
        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DETAILS_SHEET_CSV_URL, timeout=10) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP Status {resp.status}")
                csv_text = await resp.text()
                
        # Parse CSV
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        
        sem_row_idx = -1
        sem_cols = {}
        
        for r_idx, row in enumerate(rows):
            row_cleaned = [c.strip() for c in row]
            if "Redemption Dates" in row_cleaned:
                sem_row_idx = r_idx
                start_col = row_cleaned.index("Redemption Dates") + 1
                for c_idx in range(start_col, len(row_cleaned)):
                    val = row_cleaned[c_idx].upper()
                    if val in ["S7", "S5", "S3", "S1"]:
                        sem_cols[val] = c_idx
                break
                
        if sem_row_idx == -1 or not sem_cols:
            raise ValueError("Could not locate 'Redemption Dates' row or sem columns in CSV")
            
        ip1_row = None
        ip2_row = None
        
        for row in rows:
            row_cleaned = [c.strip() for c in row]
            if any("Last Day for IP 1" in cell or "IP 1 Redemption" in cell for cell in row_cleaned):
                ip1_row = row_cleaned
            elif any("Last Day for IP 2" in cell or "IP 2 Redemption" in cell for cell in row_cleaned):
                ip2_row = row_cleaned
                
        if not ip1_row or not ip2_row:
            raise ValueError("Could not find IP 1 or IP 2 deadline rows in CSV")
            
        # Extract dates
        new_dates = {}
        for sem in ["S7", "S5", "S3", "S1"]:
            col_idx = sem_cols.get(sem)
            if col_idx is not None and col_idx < len(ip1_row) and col_idx < len(ip2_row):
                ip1_val = ip1_row[col_idx].strip() if ip1_row[col_idx] and ip1_row[col_idx].strip() != "-" else "Not scheduled (-)"
                ip2_val = ip2_row[col_idx].strip() if ip2_row[col_idx] and ip2_row[col_idx].strip() != "-" else "Not scheduled (-)"
                new_dates[sem] = {
                    "ip1": ip1_val,
                    "ip2": ip2_val
                }
            else:
                new_dates[sem] = {
                    "ip1": "Not scheduled (-)" if sem == "S1" else DEFAULT_REDEMPTION_DATES[sem]["ip1"],
                    "ip2": "Not scheduled (-)" if sem == "S1" else DEFAULT_REDEMPTION_DATES[sem]["ip2"]
                }
                
        _dates_cache = new_dates
        _cache_expiry = now + CACHE_DURATION
        print("Successfully updated redemption dates from live Google Sheet CSV.")
        return _dates_cache
        
    except Exception as e:
        print(f"Error fetching live redemption dates: {e}. Using cached/fallback dates.")
        if _dates_cache:
            return _dates_cache
        return DEFAULT_REDEMPTION_DATES

async def get_redemption_dates(year):
    # Convert year to string and clean it
    yr_str = str(year).strip().upper()
    
    # Fetch current dates mapping
    dates_map = await fetch_live_redemption_dates()
    
    # Mapping based on semester / year
    if yr_str in ["IV", "4", "4TH"]:
        sem = "S7"
    elif yr_str in ["III", "3", "3RD"]:
        sem = "S5"
    elif yr_str in ["II", "2", "2ND", "II L", "II-L", "2 L", "2-L", "IIL"]:
        sem = "S3"
    elif yr_str in ["I", "1", "1ST"]:
        sem = "S1"
    else:
        return None
        
    dates = dates_map.get(sem)
    if not dates:
        return None
        
    return {
        "sem": sem,
        "ip1": dates.get("ip1", "Not scheduled (-)"),
        "ip2": dates.get("ip2", "Not scheduled (-)")
    }

async def format_report(data):
    # Emojis for status
    status = data.get('status', '-').strip()
    status_emoji = "✅" if "pass" in status.lower() or "active" in status.lower() else "ℹ️"
    
    html = (
        f"💳 <b>REWARD POINTS REPORT</b>\n"
        f"───────────────────\n"
        f"👤 <b>Student:</b> {data.get('studentName', '-')}\n"
        f"🆔 <b>Roll No:</b> <code>{data.get('roll', '-')}</code>\n"
        f"🏢 <b>Dept:</b> {data.get('department', '-')} ({data.get('year', '-')} Year)\n"
        f"🤝 <b>Mentor:</b> {data.get('mentor', '-')}\n\n"
        
        f"📊 <b>POINTS SUMMARY</b>\n"
        f" ├ 🌟 <b>Cumulative:</b> <code>{data.get('cumPoints', 0)}</code> pts\n"
        f" ├ 🛍️ <b>Redeemed:</b> <code>{data.get('redeemed', 0)}</code> pts\n"
        f" ├ 📈 <b>Class Average:</b> <code>{data.get('yearAvg', 0)}</code> pts\n"
        f" └ 💰 <b>Current Balance:</b> <b>{data.get('balance', 0)}</b> pts\n\n"
    )
    
    redemption = await get_redemption_dates(data.get('year'))
    if redemption:
        html += (
            f"📅 <b>REDEMPTION DEADLINES ({redemption['sem']})</b>\n"
            f" ├ ⏳ <b>IP 1 Limit:</b> <code>{redemption['ip1']}</code>\n"
            f" └ ⌛ <b>IP 2 Limit:</b> <code>{redemption['ip2']}</code>\n\n"
        )
        
    html += (
        f"───────────────────\n"
        f"📢 <b>Status:</b> {status_emoji} <b>{status}</b>"
    )
    return html

async def fetch_and_send_report(chat_id: int, user, roll: str, context: ContextTypes.DEFAULT_TYPE, reply_to_message_id: int = None):
    try:
        wait_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Fetching your data...", reply_to_message_id=reply_to_message_id)
    except Exception:
        wait_msg = None

    if not SHEET_API_URL:
        msg_text = "❌ SHEET_API_URL is not configured."
        if wait_msg:
            await wait_msg.edit_text(msg_text)
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg_text)
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SHEET_API_URL.rstrip("/"), params={"rollNo": roll}, timeout=15) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Upstream {resp.status}: {text[:200]}")
                data = await resp.json()
    except Exception as e:
        msg_text = f"❌ Error calling API: {e}"
        if wait_msg:
            await wait_msg.edit_text(msg_text)
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg_text)
        return

    if not data.get("success"):
        msg_text = "❌ " + (data.get("error") or "Student not found.")
        if wait_msg:
            await wait_msg.edit_text(msg_text)
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg_text)
        return

    # save report in mongo
    try:
        await save_report(user.id, roll, data["data"])
    except Exception as e:
        msg_text = f"❌ DB error: {e}"
        if wait_msg:
            await wait_msg.edit_text(msg_text)
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg_text)
        return

    # Format and send report
    keyboard = [
        [
            InlineKeyboardButton("Check another roll", callback_data="check_another"),
            InlineKeyboardButton("Contact Admin", url="https://t.me/testbitbot1")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if wait_msg:
            await wait_msg.edit_text(text=await format_report(data["data"]), parse_mode="HTML", reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=chat_id, text=await format_report(data["data"]), parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        pass

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "check_another":
        await query.message.reply_html("📩 Please send your Roll Number (e.g. <code>7376221CS259</code>) to fetch your report.")
    elif query.data.startswith("check_saved_"):
        roll = query.data.split("check_saved_")[1]
        await fetch_and_send_report(update.effective_chat.id, query.from_user, roll, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_bot(user):
        return
    text = update.message.text.strip()
    if not text:
        return
        
    if text == "🔍 Check Points":
        await update.message.reply_html(
            "📩 Please send your Roll Number (e.g. <code>7376221CS259</code>) to fetch your report."
        )
        return
    elif text == "🕒 My Last Report":
        await last_report(update, context)
        return

    await fetch_and_send_report(update.effective_chat.id, user, text, context, reply_to_message_id=update.message.message_id)

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
    await update.message.reply_text(await format_report(data), parse_mode="HTML", reply_markup=reply_markup)

# Register handlers only if bot is initialized
if app_bot is not None:
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("stats", stats))
    app_bot.add_handler(CommandHandler("exportusers", export_users))
    app_bot.add_handler(CommandHandler("broadcast", broadcast))
    app_bot.add_handler(CommandHandler("lastreport", last_report))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    # /dbstatus command to diagnose DB connectivity
    async def dbstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_bot(update.effective_user) or update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ You are not authorized.")
            return
        try:
            from api.db import ping_db_sync, get_collection
            import asyncio
            pong = await asyncio.to_thread(ping_db_sync)
            users_col = get_collection("users")
            total = await asyncio.to_thread(users_col.count_documents, {})
            await update.message.reply_text(f"✅ DB OK: {pong}. Users: {total}")
        except Exception as e:
            await update.message.reply_text(f"❌ DB error: {e}")
    app_bot.add_handler(CommandHandler("dbstatus", dbstatus))
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
