import os
import requests
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram import Update
from telegram.ext import ContextTypes
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

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_API_URL = os.getenv("SHEET_API_URL")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! Send your roll number (e.g., 7376221CS259) to get your student report."
    )

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    roll = update.message.text.strip()
    if not roll:
        await update.message.reply_text("Please send a roll number.")
        return

    try:
        resp = requests.get(SHEET_API_URL, params={"rollNo": roll}, timeout=30)  # increased timeout
        data = resp.json()
        print(data)  # optional: debug
    except Exception as e:
        await update.message.reply_text(f"❌ Error calling API: {e}")
        return

    if not data.get("success"):
        await update.message.reply_text("❌ " + (data.get("error") or "Student not found."))
        return

    msg = format_report(data["data"])
    await update.message.reply_text(msg, parse_mode="HTML")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()
