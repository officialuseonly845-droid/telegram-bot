import logging
import os
import re
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")

# Flask app to keep Render happy
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! Send me your X link or type 'ad'/'done' and I'll reply with your handle.")

# Handle messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    trigger_words = ["ad", "Ad", "AD", "done", "Done", "DONE", "all done", "All done", "ALL DONE"]

    # Find X username
    x_match = re.search(r"x\.com/([A-Za-z0-9_]+)", text)
    x_username = f"@{x_match.group(1)}" if x_match else None

    if any(word in text for word in trigger_words) or x_username:
        if x_username:
            await update.message.reply_text(
                f"Your X ID - {x_username}\n\nYour X profile - {x_username}"
            )
        else:
            await update.message.reply_text("Please include your X link!")

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def run_bot():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    application.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
