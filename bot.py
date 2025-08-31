import os
import re
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram token from Replit secrets
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Global sets to store users
link_senders = set()
ad_senders = set()

# Commands
async def start_slot(update: Update, context):
    await update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = True

async def stop_detect(update: Update, context):
    await update.message.reply_text("STOP ⛔ SENDING LINK 🔗")
    context.chat_data['collecting_links'] = False
    context.chat_data['detect_ads'] = True

async def list_users(update: Update, context):
    if link_senders:
        msg = "Users who sent X links:\n" + "\n".join(link_senders)
    else:
        msg = "No X links detected yet."
    await update.message.reply_text(msg)

async def ad_list(update: Update, context):
    if ad_senders:
        msg = "Users who sent Ads:\n" + "\n".join(ad_senders)
    else:
        msg = "No Ads detected yet."
    await update.message.reply_text(msg)

async def not_ad_list(update: Update, context):
    not_ads = link_senders - ad_senders
    if not_ads:
        msg = "Users who DIDN'T send ads:\n" + "\n".join(not_ads)
    else:
        msg = "No users without ads."
    await update.message.reply_text(msg)

async def refresh(update: Update, context):
    link_senders.clear()
    ad_senders.clear()
    await update.message.reply_text("All lists cleared! ✅")

async def double_check(update: Update, context):
    msg_counts = {}
    for user in link_senders:
        msg_counts[user] = msg_counts.get(user, 0) + 1
    doubles = [u for u, count in msg_counts.items() if count > 1]
    if doubles:
        await update.message.reply_text("Users who sent 2+ links:\n" + "\n".join(doubles))
    else:
        await update.message.reply_text("No users sent 2+ links.")

# Detect messages
async def handle_message(update: Update, context):
    username = "@" + (update.message.from_user.username or update.message.from_user.first_name)
    text = update.message.text or ""

    if context.chat_data.get('collecting_links') and "x.com/" in text:
        link_senders.add(username)

    if context.chat_data.get('detect_ads') and re.search(r'\b(ad|Ad|AD)\b', text):
        ad_senders.add(username)
        await update.message.reply_text(f"Your X ID - {username}")

# Initialize bot
application = Application.builder().token(TOKEN).build()

# Add handlers
application.add_handler(CommandHandler("slot", start_slot))
application.add_handler(CommandHandler("detect", stop_detect))
application.add_handler(CommandHandler("list", list_users))
application.add_handler(CommandHandler("adlist", ad_list))
application.add_handler(CommandHandler("notad", not_ad_list))
application.add_handler(CommandHandler("refresh", refresh))
application.add_handler(CommandHandler("double", double_check))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Run bot
if __name__ == "__main__":
    application.run_polling()


   
    
 

   
