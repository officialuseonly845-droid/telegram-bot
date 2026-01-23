import os
import logging
import random
import threading
import html
from datetime import datetime, time, timedelta
from threading import Thread
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Data Storage ---
daily_locks = {}
active_chats = set()
lock_mutex = threading.Lock()

# --- Helpers ---
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def safe_h(text):
    return html.escape(text or "Unknown Entity")

def reset_and_track(chat_id):
    today = get_ist_time().date()
    with lock_mutex:
        active_chats.add(chat_id)
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {'date': today, 'commands': {}, 'used_users': set()}

async def get_unique_random_member(update: Update, chat_id):
    """Picks a member not yet chosen today. If all are chosen, clears the list to allow reuse."""
    try:
        admins = await update.effective_chat.get_administrators()
        human_members = [admin.user for admin in admins if not admin.user.is_bot]
        
        # Get IDs of users already used today in this chat
        used_ids = daily_locks[chat_id]['used_users']
        
        # Filter members who haven't been picked yet
        available = [u for u in human_members if u.id not in used_ids]
        
        # If no one is left, reset the 'used' list for this chat so commands keep working
        if not available:
            logger.info(f"All members used in {chat_id}. Resetting used list.")
            daily_locks[chat_id]['used_users'] = set()
            available = human_members
            
        return random.choice(available) if available else update.effective_user
    except Exception as e:
        logger.error(f"Error picking member: {e}")
        return update.effective_user

# --- Handlers ---
async def handle_fun_command(update: Update, cmd_name, messages_list, has_pct=False):
    chat_id = update.effective_chat.id
    reset_and_track(chat_id)
    
    with lock_mutex:
        locked_cmd = daily_locks[chat_id]['commands'].get(cmd_name)

    if locked_cmd:
        # Show the same result for 24 hours
        await update.message.reply_text(f"📌 <b>Daily Record:</b>\n{locked_cmd['message']}", parse_mode=ParseMode.HTML)
    else:
        # Pick a NEW member from the remaining list
        user = await get_unique_random_member(update, chat_id)
        u_disp = f"@{safe_h(user.username)}" if user.username else f"<b>{safe_h(user.first_name)}</b>"
        pct = random.randint(0, 100) if has_pct else None
        
        msg = random.choice(messages_list).format(user=u_disp, pct=pct)
        
        with lock_mutex:
            daily_locks[chat_id]['commands'][cmd_name] = {'message': msg}
            # LOCK this user so no other command picks them today
            daily_locks[chat_id]['used_users'].add(user.id)
            
        await update.message.reply_text(f"✨ {msg}", parse_mode=ParseMode.HTML)

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0].replace('/', '').split('@')[0].lower()
    
    # --- Expanded Command Mapping ---
    mapping = {
        "gay": (["🌈 Today's gay is {user}! ({pct}% gay) 🌚✨", "🦄 {user} is feeling fabulous! {pct}% 🏳️‍🌈💅"], True),
        "roast": (["💀 {user} got roasted harder than a marshmallow! 🔥🍗", "🗑️ {user} is the reason the gene pool needs a lifeguard! 🏊‍♂️💀"], False),
        "simp": (["💘 {user} is today's official SIMP! 😍📈", "🐕 {user} is barking for attention today! 🦴💦"], False),
        "legend": (["👑 {user} is THE LEGEND today! 😎⚡", "🌟 All hail {user}, today's ICON! 👑🙌"], False),
        "noob": (["🍼 {user} is today's official NOOB! 😂📉", "🕹️ {user} is lagging in real life! 🌐🐢"], False),
        "brain": (["🧠 {user}'s brain power: {pct}% 🤯🔋", "💡 {user} has a lightbulb moment at {pct}% brightness! 🕯️"], True),
        "sus": (["🤔 {user} is acting SUS today! 🚨🕵️", "🚨 {user} = Imposter vibes detected! 🤡🔪"], False),
        "foodie": (["🍔 {user} is today's group FOODIE! 🍕🍰", "🍜 {user} is always HUNGRY! 😂🍟"], False),
        "dance": (["🕺 {user} is DANCING! 💃🔥", "🪩 {user} has got the moves! 💃🎵"], False),
        "monkey": (["🐒 {user} is the group MONKEY! 🙈🍌", "🐵 {user} needs a zoo immediately! 😂🙊"], False),
        "luck": (["🍀 {user}'s luck today: {pct}% 🎲💸", "🎰 {user} hit the jackpot with {pct}% luck! 🔥✨"], True)
    }
    
    if cmd in mapping:
        msgs, has_pct = mapping[cmd]
        await handle_fun_command(update, cmd, msgs, has_pct)

# --- Server & Main ---
@app.route('/')
def health(): return jsonify({"status": "running"})

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token: return
    Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(token).build()
    
    # Jobs (7 AM and 11 PM IST)
    application.job_queue.run_daily(lambda c: logger.info("Morning Wish"), time=time(1, 30))
    application.job_queue.run_daily(lambda c: logger.info("Night Wish"), time=time(17, 30))

    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Active! 🚀")))
    
    fun_list = ["gay", "roast", "simp", "legend", "noob", "brain", "sus", "foodie", "dance", "monkey", "luck", "sleep", "dead", "cap", "random", "mirror"]
    for cmd in fun_list:
        application.add_handler(CommandHandler(cmd, cmd_dispatcher))

    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
