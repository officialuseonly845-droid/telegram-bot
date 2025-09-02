import os
import re
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging for debugging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Token from environment variables
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hey! Send your ad or 'done' and I'll grab your X username!")

# Extract username
def extract_x_username(text: str) -> str:
    match = re.search(r"x\.com/([A-Za-z0-9_]+)", text)
    if match:
        return f"@{match.group(1)}"
    return None

# Handle messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    trigger_words = ["ad", "done", "all done"]

    if any(t.lower() == text.lower() for t in trigger_words):
        username = extract_x_username(text)
        if username:
            reply = f"Your X ID - {username}\n\nYour X Profile - {username}"
        else:
            reply = "⚠️ No valid X link found. Please send a correct one."
        await update.message.reply_text(reply)

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

# Main
def main():
    if not TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN is missing! Set it in Render env vars.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("✅ Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
