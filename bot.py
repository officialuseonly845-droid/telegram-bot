from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
import logging
import re
from keep_alive import keep_alive

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "YOUR_BOT_TOKEN"  # 🔹 Replace with your bot token

# Data stores
link_senders = {}
ad_senders = {}

# Commands
async def slot(update: Update, context):
    await update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = True

async def detect(update: Update, context):
    await update.message.reply_text("⛔ STOP SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = False
    context.chat_data['detect_ads'] = True

async def refresh(update: Update, context):
    link_senders.clear()
    ad_senders.clear()
    await update.message.reply_text("LIST ♻ REFRESHED")

async def handle_message(update: Update, context):
    username = "@" + (update.message.from_user.username or update.message.from_user.first_name)
    text = update.message.text or ""

    if context.chat_data.get('collecting_links') and "x.com/" in text:
        link_senders[username] = text

    if context.chat_data.get('detect_ads') and re.search(r'\b(ad|Ad|AD|done|all done)\b', text):
        x_link = link_senders.get(username, "")
        x_user, x_profile = "Unknown", "Unknown"
        if "x.com/" in x_link:
            try:
                parts = x_link.split("x.com/")[1].split("/")
                x_user = f"@{parts[0]}"
                x_profile = f"https://x.com/{parts[0]}"
            except:
                pass
        ad_senders[username] = x_user
        await update.message.reply_text(
            f"ENGAGEMENT RECORDED FROM ✅\nX ID - {x_user}\n\nX profile - {x_profile}"
        )

async def list_users(update: Update, context):
    users = "\n".join(link_senders.keys()) or "No users yet."
    await update.message.reply_text(f"USERS PARTICIPATED ✅\n{users}")

async def ad_list(update: Update, context):
    users = "\n".join(ad_senders.values()) or "No ads yet."
    await update.message.reply_text(f"📌 THESE PEOPLE HAVE COMPLETED ENGAGEMENT\n{users}")

async def notad_list(update: Update, context):
    not_ads = [u for u in link_senders if u not in ad_senders]
    users = "\n".join(not_ads) or "Everyone has sent ads."
    await update.message.reply_text(f"NOT SENT ADS:\n{users}")

def main():
    keep_alive()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("slot", slot))
    app.add_handler(CommandHandler("detect", detect))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("adlist", ad_list))
    app.add_handler(CommandHandler("notad", notad_list))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
