import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import re

TOKEN = os.getenv("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! Send me your ad link or type 'done' when finished.")

def extract_x_username(text):
    match = re.search(r"https?://(?:www\.)?x\.com/([A-Za-z0-9_]+)", text)
    return f"@{match.group(1)}" if match else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    trigger_words = ["ad", "Ad", "ALL DONE", "All done", "all done", "Done", "done"]

    if any(word in text for word in trigger_words):
        username = extract_x_username(text)
        if username:
            await update.message.reply_text(f"Your X ID - {username}")
            await update.message.reply_text(f"Your X profile - {username}")
        else:
            await update.message.reply_text("Couldn't find an X username in your message.")
    else:
        await update.message.reply_text("Please send a valid ad link or type 'done'.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
