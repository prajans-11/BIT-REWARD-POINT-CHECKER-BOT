import os
import aiohttp
import asyncio
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from dotenv import load_dotenv

from flask import Flask
from threading import Thread

# --- Keep-alive server ---
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- Load environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_API_URL = os.getenv("SHEET_API_URL")

# --- Start command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! Send your roll number (e.g., 7376221CS259) to get your student report."
    )

# --- Format the report ---
def format_report(data):
    lines = [
        f"Roll No        : {data.get('roll','-')}",
        f"Student Name   : {data.get('studentName','-')}",
        f"Course Code    : {data.get('courseCode','-')}",
        f"Department     : {data.get('department','-')}",
        f"Year           : {data.get('year','-')}",
        f"Mentor         : {data.get('mentor','-')}",
        f"Cum. Points    : {data.get('cumPoints',0)}",
        f"Redeemed       : {data.get('redeemed',0)}",
        f"Balance        : {data.get('balance',0)}",
        f"Year Avg       : {data.get('yearAvg',0)}",
        f"Status         : {data.get('status','-')}"
    ]
    return "<pre>\n" + "\n".join(lines) + "\n</pre>"

# --- Handle user messages with progress bar ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    roll = update.message.text.strip()
    if not roll:
        await update.message.reply_text("Please send a roll number.")
        return

    # Send initial wait message
    wait_msg = await update.message.reply_text("⏳ Fetching your data...\n[□□□□] 0%")

    # Progress bar steps
    progress_steps = [
        "[■□□□] 25%",
        "[■■□□] 50%",
        "[■■■□] 75%",
        "[■■■■] 100%"
    ]

    # Function to animate progress bar
    async def animate_progress(msg):
        try:
            for step in progress_steps:
                await msg.edit_text(f"⏳ Fetching your data...\n{step}")
                await asyncio.sleep(0.5)  # delay between steps
        except:
            pass  # message already edited with final data

    # Start animation concurrently
    animation_task = asyncio.create_task(animate_progress(wait_msg))

    try:
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Fetch data asynchronously
        async with aiohttp.ClientSession() as session:
            async with session.get(SHEET_API_URL, params={"rollNo": roll}, timeout=30) as resp:
                data = await resp.json()
    except Exception as e:
        animation_task.cancel()
        await wait_msg.edit_text(f"❌ Error calling API: {e}")
        return

    animation_task.cancel()  # stop animation

    if not data.get("success"):
        await wait_msg.edit_text("❌ " + (data.get("error") or "Student not found."))
        return

    # Format report
    msg = format_report(data["data"])
    await wait_msg.edit_text(msg, parse_mode="HTML")

# --- Main function ---
def main():
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("Bot started...")
    app_bot.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()
