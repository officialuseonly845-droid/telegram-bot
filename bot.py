import os
import re
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram bot token from environment variable
TOKEN = os.getenv("TELEGRAM_TOKEN")  # Set this in Replit Secrets or Render Secrets

# Global storage
link_senders = set()   # Telegram usernames who sent X links
x_links = {}           # Telegram username -> X username from link (@ prefixed)
ad_senders = set()     # Telegram usernames who sent ads

# --- Commands ---
async def start_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = True

async def stop_detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("STOP ⛔ SENDING LINK 🔗")
    context.chat_data['collecting_links'] = False
    context.chat_data['detect_ads'] = True

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if link_senders:
        msg = "Users who sent X links:\n" + "\n".join(link_senders)
    else:
        msg = "No X links detected yet."
    await update.message.reply_text(msg)

async def ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ad_senders:
        msg = "Users who sent Ads:\n" + "\n".join(ad_senders)
    else:
        msg = "No Ads detected yet."
    await update.message.reply_text(msg)

async def not_ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    not_ads = link_senders - ad_senders
    if not_ads:
        msg = "Users who DIDN'T send ads:\n" + "\n".join(not_ads)
    else:
        msg = "No users without ads."
    await update.message.reply_text(msg)

async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_senders.clear()
    ad_senders.clear()
    x_links.clear()
    await update.message.reply_text("All lists cleared! ✅")

async def double_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_counts = {}
    for user in link_senders:
        msg_counts[user] = msg_counts.get(user, 0) + 1
    doubles = [u for u, count in msg_counts.items() if count > 1]
    if doubles:
        await update.message.reply_text("Users who sent 2+ links:\n" + "\n".join(doubles))
    else:
        await update.message.reply_text("No users sent 2+ links.")

# --- Handle messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_username = "@" + (update.message.from_user.username or update.message.from_user.first_name)
    text = update.message.text or ""

    # Detect X links
    if context.chat_data.get('collecting_links') and "x.com/" in text:
        link_senders.add(telegram_username)
        match = re.search(r"x\.com/([\w\d_]+)", text)
        if match:
            x_username = "@" + match.group(1)
            x_links[telegram_username] = x_username

    # Detect Ads or "all done" triggers
    triggers = r'\b(ad|Ad|AD|all done|All done|ALL DONE|done|Done)\b'
    if context.chat_data.get('detect_ads') and re.search(triggers, text):
        ad_senders.add(telegram_username)
        x_username = x_links.get(telegram_username, "Unknown")
        profile_url = f"https://x.com/{x_username.lstrip('@')}" if x_username != "Unknown" else "Unknown"
        await update.message.reply_text(
            f"Your X ID - {x_username}\nYour X profile - {profile_url}"
        )

# --- Initialize bot ---
application = Application.builder().token(TOKEN).build()

# Add handlers
application.add_handler(CommandHandler("slot", start_slot))
application.add_handler(CommandHandler("detect", stop_detect))
application.add_handler(CommandHandler("list", list_users))
application.add_handler(CommandHandler("adlist", ad_list))
application.add_handler(CommandHandler("notad", not_ad_list))
application.add_handler(CommandHandler("refresh", refresh))
application.add_handler(CommandHandler("double", double_check))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- Start bot ---
if __name__ == "__main__":
    application.run_polling()
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run).start()

    
 

   
