import os
import requests
import asyncio
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram import Update
from telegram.ext import ContextTypes
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
from telegram.constants import ChatAction

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

# --- Format report with sections ---
def format_report(data):
    lines = [
        "===== Academic Info =====",
        f"Roll No      : <b>{data.get('roll','-')}</b>",
        f"Name         : <b>{data.get('studentName','-')}</b>",
        f"Course       : <b>{data.get('courseCode','-')}</b> ({data.get('department','-')})",
        f"Year         : <b>{data.get('year','-')}</b>",
        f"Mentor       : <b>{data.get('mentor','-')}</b>",
        "",
        "===== Points Summary =====",
        f"Cum. Points  : <b>{data.get('cumPoints',0)}</b>",
        f"Redeemed     : <b>{data.get('redeemed',0)}</b>",
        f"Balance      : <b>{data.get('balance',0)}</b>",
        f"Year Avg     : <b>{data.get('yearAvg',0)}</b>",
        f"Status       : <b>{data.get('status','-')}</b>"
    ]
    return "\n".join(lines)

# --- Handle messages dynamically ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    roll = update.message.text.strip()
    if not roll:
        await update.message.reply_text("Please send a roll number.")
        return

    # Show typing animation
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await asyncio.sleep(1)  # simulate delay

    # Fetch student data
    try:
        resp = requests.get(SHEET_API_URL, params={"rollNo": roll}, timeout=30)
        data = resp.json()
        print(data)
    except Exception as e:
        await update.message.reply_text(f"❌ Error calling API: {e}")
        return

    if not data.get("success"):
        await update.message.reply_text("❌ " + (data.get("error") or "Student not found."))
        return

    student = data["data"]
    avg = student.get("yearAvg", 0)

    # --- Select GIF based on performance ---
    if avg >= 90:
        gif_url = "https://media.giphy.com/media/111ebonMs90YLu/giphy.gif"  # celebration
        performance_msg = "🏆 Top Performer! Keep it up!"
    elif avg >= 70:
        gif_url = "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif"  # good
        performance_msg = "👍 Good performance, aim higher!"
    else:
        gif_url = "https://media.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif"  # motivational
        performance_msg = "⚠ Keep pushing! You can do better!"

    # Send GIF animation
    await context.bot.send_animation(chat_id=update.effective_chat.id, animation=gif_url)
    await asyncio.sleep(1)  # brief pause for GIF

    # Send report line by line for dynamic effect
    report = format_report(student)
    for line in report.split("\n"):
        await update.message.reply_text(line, parse_mode="HTML")
        await asyncio.sleep(0.2)  # slight delay per line

    # Send performance message at the end
    await update.message.reply_text(performance_msg)

# --- Main function ---
def main():
    app_builder = ApplicationBuilder().token(BOT_TOKEN).build()
    app_builder.add_handler(CommandHandler("start", start))
    app_builder.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("Bot started...")
    app_builder.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()
