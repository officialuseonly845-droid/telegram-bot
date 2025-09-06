import os
import re
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token from environment
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Global sets to store users
link_senders = set()
ad_senders = set()
user_links = {}  # Store mapping telegram username -> X link

# Commands
async def slot(update: Update, context):
    await update.message.reply_text("START SENDING LINKS ✅")
    context.chat_data['collecting_links'] = True

async def detect(update: Update, context):
    await update.message.reply_text("⛔ STOP SENDING LINKS")
    context.chat_data['collecting_links'] = False
    context.chat_data['detect_ads'] = True

async def list_users(update: Update, context):
    msg = "USERS PARTICIPATED ✅\n"
    msg += "\n".join(link_senders) if link_senders else "No users yet."
    await update.message.reply_text(msg)

async def adlist(update: Update, context):
    msg = "📌 THESE PEOPLE HAVE COMPLETED ENGAGEMENT\n"
    msg += "\n".join(ad_senders) if ad_senders else "No engagements yet."
    await update.message.reply_text(msg)

async def notad(update: Update, context):
    not_ads = link_senders - ad_senders
    msg = "\n".join(not_ads) if not_ads else "Everyone completed engagement."
    await update.message.reply_text(msg)

async def refresh(update: Update, context):
    link_senders.clear()
    ad_senders.clear()
    user_links.clear()
    await update.message.reply_text("LIST ♻ REFRESHED")

# Detect messages
async def handle_message(update: Update, context):
    username = "@" + (update.message.from_user.username or update.message.from_user.first_name)
    text = update.message.text or ""

    # Collect X links
    if context.chat_data.get('collecting_links') and "x.com/" in text:
        link_senders.add(username)
        user_links[username] = text.strip()

    # Detect ads or done messages
    if context.chat_data.get('detect_ads') and re.search(r'\b(ad|Ad|AD|done|Done|all done|All done|ALL DONE)\b', text):
        if username in user_links:
            x_link = user_links[username]
            # Extract X username from link
            x_username = x_link.split("x.com/")[-1].split("/")[0]
            x_profile = x_link.split("/status")[0]  # get profile link part
            ad_senders.add(username)
            reply = f"ENGAGEMENT RECORDED FROM ✅\nX ID - @{x_username}\nX profile - {x_profile}"
            await update.message.reply_text(reply)

# Initialize bot
application = Application.builder().token(TOKEN).build()

# Add handlers
application.add_handler(CommandHandler("slot", slot))
application.add_handler(CommandHandler("detect", detect))
application.add_handler(CommandHandler("list", list_users))
application.add_handler(CommandHandler("adlist", adlist))
application.add_handler(CommandHandler("notad", notad))
application.add_handler(CommandHandler("refresh", refresh))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Error handler to keep bot alive
async def error_handler(update: object, context):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

application.add_error_handler(error_handler)

# Run bot
if __name__ == "__main__":
    application.run_polling()
