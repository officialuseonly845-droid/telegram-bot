from flask import Flask
from threading import Thread
import os
import re
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Keep Alive Web Server ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Telegram Bot Token ---
TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- Data Stores ---
link_senders = {}
ad_senders = {}

# --- Commands ---
async def start_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = True

async def stop_detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ STOP SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = False
    context.chat_data['detect_ads'] = True

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if link_senders:
        msg = "USERS PARTICIPATED ✅\n" + "\n".join(link_senders.keys())
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("No links yet.")

async def ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ad_senders:
        msg = "📌 THESE PEOPLE HAVE COMPLETED ENGAGEMENT\n" + "\n".join(ad_senders.values())
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("No ads yet.")

async def not_ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    not_ads = [u for u in link_senders.keys() if u not in ad_senders]
    if not_ads:
        msg = "Users without ads:\n" + "\n".join(not_ads)
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("Everyone sent ads.")

async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_senders.clear()
    ad_senders.clear()
    await update.message.reply_text("LIST ♻ REFRESHED")

# --- Helper to extract X username ---
def extract_x_username(text):
    match = re.search(r"x\.com/([A-Za-z0-9_]+)", text)
    return match.group(1) if match else "unknown"

# --- Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = "@" + (update.message.from_user.username or update.message.from_user.first_name)
    text = update.message.text or ""

    # Store X links
    if context.chat_data.get('collecting_links') and "x.com/" in text:
        x_username = extract_x_username(text)
        link_senders[username] = x_username

    # Detect Ads/Done/All Done
    if context.chat_data.get('detect_ads') and re.search(r'\b(ad|done|all done)\b', text, re.IGNORECASE):
        x_username = link_senders.get(username, "Unknown")
        ad_senders[username] = x_username
        await update.message.reply_text(
            f"ENGAGEMENT RECORDED FROM ✅\nX ID - @{x_username}\nX Profile - https://x.com/{x_username}"
        )

# --- Main Function ---
def main():
    keep_alive()  # Start web server

    app_telegram = Application.builder().token(TOKEN).build()

    app_telegram.add_handler(CommandHandler("slot", start_slot))
    app_telegram.add_handler(CommandHandler("detect", stop_detect))
    app_telegram.add_handler(CommandHandler("list", list_users))
    app_telegram.add_handler(CommandHandler("adlist", ad_list))
    app_telegram.add_handler(CommandHandler("notad", not_ad_list))
    app_telegram.add_handler(CommandHandler("refresh", refresh))
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app_telegram.run_polling()

if __name__ == "__main__":
    main()
