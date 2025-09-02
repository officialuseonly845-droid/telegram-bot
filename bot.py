import os
import logging
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Token
TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(TOKEN)

# Flask app
app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# Error handler
async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)

application.add_error_handler(error_handler)

# Start command
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Hey! Send your ad or say 'done' and I’ll extract your X username!")

# Reply logic
async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text
    user_text = text.strip().lower()

    # Keywords
    trigger_words = ["ad", "done", "all done"]
    if any(word in user_text for word in trigger_words):
        # Extract X username from message
        username = None
        if "x.com/" in text:
            username = text.split("x.com/")[1].split("/")[0]
        
        if username:
            reply = f"Your X ID - @{username}\n\nYour X profile - https://x.com/{username}"
        else:
            reply = "Couldn't find your X username in the message. Please include your X link."

        await update.message.reply_text(reply)

# Add handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Flask routes
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.update_queue.put_nowait(update)
    return "ok"

@app.route("/", methods=["GET"])
def index():
    return "Bot is running!"

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=PORT)
