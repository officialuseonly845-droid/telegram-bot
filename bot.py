import os
import re
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Start Command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hi! Send me your ad or X link and I’ll fetch your X ID!")

# --- Extract X Username Function ---
def extract_x_username(text: str) -> str:
    match = re.search(r"https?://x\.com/([A-Za-z0-9_]+)", text)
    if match:
        return f"@{match.group(1)}"
    return None

# --- Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    trigger_words = ["ad", "all done", "done"]

    if any(word.lower() == text.lower() for word in trigger_words):
        username = extract_x_username(text)
        if username:
            reply = f"Your X ID - {username}\n\nYour X Profile - {username}"
        else:
            reply = "⚠️ I couldn't find an X link in your message!"
        await update.message.reply_text(reply)

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- Main Function ---
def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        raise ValueError("⚠️ TELEGRAM_TOKEN not set in environment variables!")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
