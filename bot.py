import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")

# Track users
link_senders = set()
ad_senders = set()

# === Commands ===

async def slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Slot command triggered")

async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Detect mode enabled")
    context.chat_data["detect_ads"] = True

async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_senders.clear()
    ad_senders.clear()
    await update.message.reply_text("All lists cleared ✅")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if link_senders:
        await update.message.reply_text("\n".join(link_senders))
    else:
        await update.message.reply_text("")

async def adlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ad_senders:
        await update.message.reply_text("\n".join(ad_senders))
    else:
        await update.message.reply_text("")

async def notad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    not_ads = link_senders - ad_senders
    if not_ads:
        await update.message.reply_text("\n".join(not_ads))
    else:
        await update.message.reply_text("")

# === Message Handler ===

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    username = "@" + (update.message.from_user.username or update.message.from_user.first_name)

    triggers = ["ad", "done", "all done"]

    if "x.com/" in text:
        link_senders.add(username)

    if any(t in text.lower() for t in triggers):
        await send_x_reply(update, text)

async def send_x_reply(update: Update, text: str):
    username = None
    if "x.com/" in text:
        try:
            username = text.split("x.com/")[1].split("/")[0]
        except IndexError:
            username = None

    if username:
        reply = f"Your X ID - @{username}\n\nYour X Profile - @{username}"
    else:
        reply = "Couldn't extract username."

    await update.message.reply_text(reply)

# === Error Handler ===

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling update:", exc_info=context.error)

# === Main ===

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("slot", slot))
    app.add_handler(CommandHandler("detect", detect))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("adlist", adlist))
    app.add_handler(CommandHandler("notad", notad))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    print("🤖 Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
