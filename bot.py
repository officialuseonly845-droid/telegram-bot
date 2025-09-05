import logging
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from keep_alive import keep_alive

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot data
participants = set()
ad_senders = {}
x_profiles = {}

# Extract X username and profile link
def extract_x_data(message: str):
    match = re.search(r"https?://x\.com/([^/]+)/status/\d+", message)
    if match:
        username = match.group(1)
        profile = f"https://x.com/{username}"
        return username, profile
    return None, None

# Commands
async def slot(update: Update, context: CallbackContext):
    username = update.effective_user.username or update.effective_user.first_name
    participants.add(username)
    await update.message.reply_text("START SENDING LINKS 🔗")

async def detect(update: Update, context: CallbackContext):
    await update.message.reply_text("⛔ STOP SENDING LINKS 🔗")

async def list_users(update: Update, context: CallbackContext):
    if participants:
        text = "USERS PARTICIPATED ✅\n" + "\n".join(participants)
    else:
        text = "No users participated yet."
    await update.message.reply_text(text)

async def ad_list(update: Update, context: CallbackContext):
    if ad_senders:
        text = "📌 THESE PEOPLE HAVE COMPLETED ENGAGEMENT\n" + "\n".join(ad_senders.keys())
    else:
        text = "No ads recorded yet."
    await update.message.reply_text(text)

async def not_ad(update: Update, context: CallbackContext):
    not_completed = [u for u in participants if u not in ad_senders]
    if not_completed:
        text = "These users have NOT sent ads:\n" + "\n".join(not_completed)
    else:
        text = "Everyone has sent ads."
    await update.message.reply_text(text)

async def refresh(update: Update, context: CallbackContext):
    participants.clear()
    ad_senders.clear()
    x_profiles.clear()
    await update.message.reply_text("LIST ♻ REFRESHED")

# Detect ad/done messages
async def detect_ads(update: Update, context: CallbackContext):
    username = update.effective_user.username or update.effective_user.first_name
    text = update.message.text

    if any(word in text.lower() for word in ["ad", "done", "all done"]):
        if username not in ad_senders:
            x_user, x_profile = extract_x_data(text)
            if x_user:
                ad_senders[username] = x_user
                x_profiles[username] = x_profile
                reply = (
                    f"ENGAGEMENT RECORDED FROM ✅\n"
                    f"X ID - @{x_user}\n\n"
                    f"X profile - {x_profile}"
                )
            else:
                reply = "ENGAGEMENT RECORDED FROM ✅\n(No valid X link found)"
            await update.message.reply_text(reply)

# Error handler
async def error_handler(update: object, context: CallbackContext):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# Start bot
def main():
    keep_alive()
    app = Application.builder().token("8411876178:AAHfnSz8lYOve1cUBlCtdnn9kokibRFA4Pg").build()

    # Handlers
    app.add_handler(CommandHandler("slot", slot))
    app.add_handler(CommandHandler("detect", detect))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("adlist", ad_list))
    app.add_handler(CommandHandler("notad", not_ad))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, detect_ads))

    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
