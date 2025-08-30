from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from flask import Flask
from threading import Thread
import re

# === BOT TOKEN ===
TOKEN = "8411876178:AAHfnSz8lYOve1cUBlCtdnn9kokibRFA4Pg"

# === LISTS TO STORE DATA ===
link_senders = set()
ad_senders = set()

# === START WEB SERVER (to keep alive on Railway) ===
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    Thread(target=run).start()

# === COMMAND FUNCTIONS ===
def start_slot(update, context):
    update.message.reply_text("START SENDING LINKS 🔗")
    context.chat_data['collecting_links'] = True

def stop_detect(update, context):
    update.message.reply_text("STOP ⛔ SENDING LINK 🔗")
    context.chat_data['collecting_links'] = False
    context.chat_data['detect_ads'] = True

def list_users(update, context):
    if link_senders:
        msg = "Users who sent X links:\n" + "\n".join(link_senders)
    else:
        msg = "No X links detected yet."
    update.message.reply_text(msg)

def ad_list(update, context):
    if ad_senders:
        msg = "Users who sent Ads:\n" + "\n".join(ad_senders)
    else:
        msg = "No Ads detected yet."
    update.message.reply_text(msg)

def not_ad_list(update, context):
    not_ads = link_senders - ad_senders
    if not_ads:
        msg = "Users who DIDN'T send ads:\n" + "\n".join(not_ads)
    else:
        msg = "No users without ads."
    update.message.reply_text(msg)

def refresh(update, context):
    link_senders.clear()
    ad_senders.clear()
    update.message.reply_text("All lists cleared! ✅")

def double_check(update, context):
    msg_counts = {}
    for user in link_senders:
        msg_counts[user] = msg_counts.get(user, 0) + 1
    doubles = [u for u, count in msg_counts.items() if count > 1]
    if doubles:
        update.message.reply_text("Users who sent 2+ links:\n" + "\n".join(doubles))
    else:
        update.message.reply_text("No users sent 2+ links.")

# === MESSAGE HANDLER ===
def handle_message(update, context):
    username = "@" + (update.message.from_user.username or update.message.from_user.first_name)
    text = update.message.text or ""

    # Detect X link
    if context.chat_data.get('collecting_links'):
        if "x.com/" in text:
            link_senders.add(username)

    # Detect Ads
    if context.chat_data.get('detect_ads'):
        if re.search(r'\b(ad|Ad|AD)\b', text):
            ad_senders.add(username)
            update.message.reply_text(f"Your X ID - {username}")

# === MAIN FUNCTION ===
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # Commands
    dp.add_handler(CommandHandler("slot", start_slot))
    dp.add_handler(CommandHandler("detect", stop_detect))
    dp.add_handler(CommandHandler("list", list_users))
    dp.add_handler(CommandHandler("adlist", ad_list))
    dp.add_handler(CommandHandler("notad", not_ad_list))
    dp.add_handler(CommandHandler("refresh", refresh))
    dp.add_handler(CommandHandler("double", double_check))

    # Messages
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    keep_alive()
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
