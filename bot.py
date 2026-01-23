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
    """Picks a member not yet chosen today. If all are chosen, clears the list."""
    try:
        admins = await update.effective_chat.get_administrators()
        human_members = [admin.user for admin in admins if not admin.user.is_bot]
        
        if not human_members:
            return update.effective_user

        used_ids = daily_locks[chat_id]['used_users']
        available = [u for u in human_members if u.id not in used_ids]
        
        # If no one new is left, reset the used list to allow repeat picks
        if not available:
            daily_locks[chat_id]['used_users'] = set()
            available = human_members
            
        return random.choice(available)
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
        # User already chosen today
        await update.message.reply_text(f"📌 <b>24H Result:</b>\n{locked_cmd['message']}", parse_mode=ParseMode.HTML)
    else:
        # Pick a NEW member
        user = await get_unique_random_member(update, chat_id)
        u_disp = f"@{safe_h(user.username)}" if user.username else f"<b>{safe_h(user.first_name)}</b>"
        pct = random.randint(0, 100) if has_pct else None
        
        msg = random.choice(messages_list).format(user=u_disp, pct=pct)
        
        with lock_mutex:
            daily_locks[chat_id]['commands'][cmd_name] = {'message': msg}
            daily_locks[chat_id]['used_users'].add(user.id)
            
        await update.message.reply_text(f"✨ {msg}", parse_mode=ParseMode.HTML)

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cmd = update.message.text.split()[0].replace('/', '').split('@')[0].lower()
    
    # --- Full Command Mapping ---
    mapping = {
        "gay": (["🌈 Today's gay is {user}! ({pct}% gay) 🌚✨", "🦄 {user} is feeling fabulous! {pct}% 🏳️‍🌈💅"], True),
        "roast": (["💀 {user} got roasted harder than a marshmallow! 🔥🍗", "🗑️ {user} is trash! 🏊‍♂️💀"], False),
        "simp": (["💘 {user} is today's SIMP! 😍📈", "🐕 {user} is barking! 🦴💦"], False),
        "legend": (["👑 {user} is THE LEGEND! 😎⚡", "🌟 {user} is the group ICON! 👑"], False),
        "noob": (["🍼 {user} is a NOOB! 😂📉", "🕹️ {user} is lagging! 🌐🐢"], False),
        "brain": (["🧠 {user}'s brain power: {pct}% 🤯🔋", "💡 IQ today: {pct}% 🕯️"], True),
        "sus": (["🤔 {user} is SUS! 🚨🕵️", "🚨 {user} = Imposter! 🤡🔪"], False),
        "foodie": (["🍔 {user} is the FOODIE! 🍕🍰", "🍜 {user} is hungry! 😂🍟"], False),
        "dance": (["🕺 {user} is DANCING! 💃🔥", "🪩 {user} has moves! 💃🎵"], False),
        "monkey": (["🐒 {user} is a MONKEY! 🙈🍌", "🐵 {user} needs a zoo! 🙊"], False),
        "luck": (["🍀 {user}'s luck: {pct}% 🎲💸", "🎰 {user} hit {pct}% luck! ✨"], True),
        "sleep": (["😴 {user} is a sleepyhead! 💤", "🛌 {user} needs coffee! ☕"], False),
        "dead": (["💀 {user} is mentally dead! 🪦", "⚰️ RIP {user}! 🕊️"], False),
        "cap": (["🧢 {user} is CAPPING! 🤥", "🤡 {user} Stop the cap! 🧢"], False),
        "random": (["🎲 {user} rating: {pct}%! 🤪", "🎰 Chaos level: {pct}%! 🎲"], True),
        "mirror": (["🪞 {user}'s mirror cracked! 💀", "🤡 Reflection needs therapy! 😭"], False),
        "cringe": (["🤡 {user} is the CRINX LORD! 🤢", "🤮 {user} = Peak Cringe! 📉"], False)
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
    
    # Register all fun commands
    fun_list = ["gay", "roast", "simp", "legend", "noob", "brain", "sus", "foodie", "dance", "monkey", "luck", "sleep", "dead", "cap", "random", "mirror", "cringe"]
    for cmd in fun_list:
        application.add_handler(CommandHandler(cmd, cmd_dispatcher))

    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
