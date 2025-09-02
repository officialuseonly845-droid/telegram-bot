import os
import re
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from keep_alive import keep_alive

# Start Flask server to keep bot alive
keep_alive()

# Logging for debugging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Bot Token
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Keywords that trigger response
TRIGGER_WORDS = ["ad", "Ad", "AD", "done", "Done", "DONE", "all done", "All done", "ALL DONE"]

# Extract X username from link
def extract_x_username(text):
    match = re.search(r"x\.com/([A-Za-z0-9_]+)", text)
    if match:
        return match.group(1)
    return None

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! Send me an X link, and I’ll extract the username for you.")

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    username = extract_x_username(text)

    if any(word in text for word in TRIGGER_WORDS) and username:
        await update.message.reply_text(f"Your X ID - @{username}\n\nYour X profile - {username}")
    elif username:
        await update.message.reply_text(f"Your X ID - @{username}\n\nYour X profile - {username}")

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# Main function
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
