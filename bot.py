import logging
import os
import re
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")

# Lists
link_senders = {}
ad_senders = set()

# Start slot
async def slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = True
    context.chat_data['detect_ads'] = False

# Stop and detect ads
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ STOP SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = False
    context.chat_data['detect_ads'] = True

# List users
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if link_senders:
        msg = "USERS PARTICIPATED ✅\n" + "\n".join(link_senders.keys())
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("No users yet.")

# Ad list
async def ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ad_senders:
        usernames = [link_senders[user] for user in ad_senders if user in link_senders]
        msg = "📌 THESE PEOPLE HAVE COMPLETED ENGAGEMENT\n" + "\n".join(usernames)
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("No ads detected yet.")

# Not ad list
async def not_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    not_ads = set(link_senders.keys()) - ad_senders
    if not_ads:
        msg = "USERS WHO DIDN'T SEND ADS:\n" + "\n".join(not_ads)
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("Everyone sent ads!")

# Refresh
async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_senders.clear()
    ad_senders.clear()
    await update.message.reply_text("LIST ♻ REFRESHED")

# Handle messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    username = f"@{update.message.from_user.username}" if update.message.from_user.username else update.message.from_user.first_name

    # Collect X link
    if context.chat_data.get('collecting_links') and "x.com/" in text:
        # Extract username
        match = re.search(r"x\.com/([A-Za-z0-9_]+)", text)
        x_username = match.group(1) if match else None
        link_senders[username] = f"@{x_username}" if x_username else username

    # Detect ads/done
    if context.chat_data.get('detect_ads') and re.search(r"\b(ad|done|all done)\b", text, re.IGNORECASE):
        ad_senders.add(username)
        x_id = link_senders.get(username, username)
        x_profile = f"https://x.com/{x_id[1:]}" if x_id.startswith("@") else x_id
        await update.message.reply_text(
            f"ENGAGEMENT RECORDED FROM ✅\nX ID - {x_id}\nX Profile - {x_profile}"
        )

# Main
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("slot", slot))
    app.add_handler(CommandHandler("detect", detect))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("adlist", ad_list))
    app.add_handler(CommandHandler("notad", not_ad))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
