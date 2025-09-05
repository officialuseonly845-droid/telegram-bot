import os
import re
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)

# Logging (for debugging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get token from Replit/Render secrets
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Global data
link_senders = {}  # {telegram_username: x_link}
ad_senders = set()

# Extract X username & profile
def extract_x_username_and_profile(link: str):
    """
    From https://x.com/username/status/123456 => returns ("@username", "https://x.com/username")
    """
    match = re.search(r"x\.com/([^/]+)/status", link)
    if match:
        username = match.group(1)
        return f"@{username}", f"https://x.com/{username}"
    return None, None

# /slot command
async def start_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data["collecting_links"] = True
    context.chat_data["detect_ads"] = False

# /detect command
async def stop_detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ STOP SENDING LINKS 🔗")
    context.chat_data["collecting_links"] = False
    context.chat_data["detect_ads"] = True

# /list command
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if link_senders:
        msg = "USERS PARTICIPATED ✅\n" + "\n".join(link_senders.keys())
    else:
        msg = "No users have sent links yet."
    await update.message.reply_text(msg)

# /adlist command
async def ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ad_senders:
        msg = "📌 THESE PEOPLE HAVE COMPLETED ENGAGEMENT\n" + "\n".join(ad_senders)
    else:
        msg = "No one has completed engagement yet."
    await update.message.reply_text(msg)

# /notad command
async def not_ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    not_ads = set(link_senders.keys()) - ad_senders
    if not_ads:
        msg = "Users who haven't sent ads:\n" + "\n".join(not_ads)
    else:
        msg = "Everyone has sent ads."
    await update.message.reply_text(msg)

# /refresh command
async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_senders.clear()
    ad_senders.clear()
    await update.message.reply_text("LIST ♻ REFRESHED")

# Handle all messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = f"@{update.message.from_user.username}" if update.message.from_user.username else update.message.from_user.first_name
    text = update.message.text or ""

    # Collect X links
    if context.chat_data.get("collecting_links") and "x.com/" in text:
        link_senders[username] = text

    # Detect "ad"/"done" messages
    if context.chat_data.get("detect_ads") and re.search(r"\b(ad|done|all done)\b", text, re.IGNORECASE):
        x_link = link_senders.get(username)
        if x_link:
            x_username, x_profile = extract_x_username_and_profile(x_link)
            if x_username:
                ad_senders.add(x_username)
                await update.message.reply_text(
                    f"ENGAGEMENT RECORDED FROM ✅\n"
                    f"X ID - {x_username}\n\n"
                    f"X profile - {x_profile}"
                )

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# Run bot
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("slot", start_slot))
    app.add_handler(CommandHandler("detect", stop_detect))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("adlist", ad_list))
    app.add_handler(CommandHandler("notad", not_ad_list))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
