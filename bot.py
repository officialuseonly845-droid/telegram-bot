import os
import re
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")

# Data storage
telegram_users = set()
user_links = {}
engaged_users = set()

# Extract X username
def extract_x_username(link: str):
    match = re.search(r"x\.com/([A-Za-z0-9_]+)", link)
    return match.group(1) if match else None

# COMMANDS
async def slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data["collecting_links"] = True
    context.chat_data["detecting_ads"] = False

async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛔ STOP SENDING LINKS 🔗")
    context.chat_data["collecting_links"] = False
    context.chat_data["detecting_ads"] = True

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "USERS PARTICIPATED ✅\n" + "\n".join(sorted(telegram_users)) if telegram_users else "No users yet."
    await update.message.reply_text(msg)

async def ad_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📌 THESE PEOPLE HAVE COMPLETED ENGAGEMENT\n" + "\n".join(sorted(engaged_users)) if engaged_users else "No engagement yet."
    await update.message.reply_text(msg)

async def not_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    not_engaged = telegram_users - {u for u in user_links if user_links[u]}
    msg = "Users who haven't sent ad:\n" + "\n".join(sorted(not_engaged)) if not_engaged else "Everyone has sent ad."
    await update.message.reply_text(msg)

async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_users.clear()
    user_links.clear()
    engaged_users.clear()
    await update.message.reply_text("LIST ♻ REFRESHED")

# MESSAGE HANDLER
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text or ""
        tg_username = (
            "@" + update.message.from_user.username
            if update.message.from_user.username
            else update.message.from_user.first_name
        )

        if context.chat_data.get("collecting_links") and "x.com/" in text:
            telegram_users.add(tg_username)
            user_links[tg_username] = text

        if context.chat_data.get("detecting_ads") and re.search(
            r"\b(ad|Ad|AD|done|Done|all done|All done|ALL DONE)\b", text
        ):
            if tg_username in user_links:
                x_link = user_links[tg_username]
                x_username = extract_x_username(x_link)
                engaged_users.add(x_username if x_username else tg_username)
                if x_username:
                    await update.message.reply_text(
                        f"ENGAGEMENT RECORDED FROM ✅\n"
                        f"X ID - @{x_username}\n\n"
                        f"X profile - https://x.com/{x_username}"
                    )
                else:
                    await update.message.reply_text(f"ENGAGEMENT RECORDED FROM ✅\nX ID - {tg_username}")
            else:
                await update.message.reply_text("Couldn't find your X link. Please send your link first.")
    except Exception as e:
        logger.error(f"Error in message handler: {e}")

# GLOBAL ERROR HANDLER
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.error(f"Update {update} caused error {context.error}")
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# MAIN APP
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("slot", slot))
    app.add_handler(CommandHandler("detect", detect))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("adlist", ad_list))
    app.add_handler(CommandHandler("notad", not_ad))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    logger.info("Bot started and running...")
    app.run_polling()

if __name__ == "__main__":
    main()
