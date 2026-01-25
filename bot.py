import os
import logging
import random
import threading
import html
from datetime import datetime, time, timedelta
from threading import Thread
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Data Storage ---
# used_users: {chat_id: {user_id: expiry_datetime}}
# seen_users: {chat_id: {user_id: user_object}}
daily_locks = {}
lock_mutex = threading.Lock()

# --- Helpers ---
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def safe_h(text):
    """Escapes HTML special characters"""
    return html.escape(text or "Unknown Entity")

def init_chat_data(chat_id):
    with lock_mutex:
        if chat_id not in daily_locks:
            daily_locks[chat_id] = {
                'commands': {},
                'used_users': {}, 
                'seen_users': {}
            }

async def get_target_member(update: Update, chat_id):
    """Picks a member excluding those picked in the last 48 hours. No tagging."""
    data = daily_locks[chat_id]
    now = get_ist_time()

    # 1. Clean expired locks (>48h)
    data['used_users'] = {uid: exp for uid, exp in data['used_users'].items() if exp > now}

    # 2. Build candidate pool
    candidates = {}
    
    # Seen users (people who typed in the group)
    for uid, user_obj in data['seen_users'].items():
        candidates[uid] = user_obj
        
    # Admins
    try:
        admins = await update.effective_chat.get_administrators()
        for admin in admins:
            if not admin.user.is_bot:
                candidates[admin.user.id] = admin.user
    except Exception: pass

    # 3. Filter: Only people NOT in used_users
    available_ids = [uid for uid in candidates.keys() if uid not in data['used_users']]

    # 4. Fallback: If everyone is locked, reset everything to keep bot functional
    if not available_ids:
        data['used_users'] = {}
        available_ids = list(candidates.keys())

    if not available_ids:
        return update.effective_user

    chosen_id = random.choice(available_ids)
    return candidates[chosen_id]

# --- Handlers ---

async def track_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memory bank: Saves users when they talk so they can be picked later"""
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    daily_locks[chat_id]['seen_users'][update.effective_user.id] = update.effective_user

async def handle_fun_command(update: Update, cmd_name, messages_list, has_pct=False):
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    now = get_ist_time()
    
    # 24h Lock for the SAME command result
    locked_cmd = daily_locks[chat_id]['commands'].get(cmd_name)
    if locked_cmd and (now - locked_cmd['time']).days < 1:
        await update.message.reply_text(f"📌 <b>Daily Result:</b>\n{locked_cmd['msg']}", parse_mode=ParseMode.HTML)
        return

    # Pick a member not used in 48 hours
    user = await get_target_member(update, chat_id)
    
    # FIX: No tagging. We use simple bold text or plain username string.
    # We remove the '@' to prevent auto-tagging by Telegram.
    u_name = safe_h(user.username) if user.username else safe_h(user.first_name)
    u_disp = f"<b>{u_name}</b>"
    
    pct = random.randint(0, 100) if has_pct else None
    msg = random.choice(messages_list).format(user=u_disp, pct=pct)
    
    # Lock for 48 hours
    expiry = now + timedelta(days=2)
    daily_locks[chat_id]['used_users'][user.id] = expiry
    daily_locks[chat_id]['commands'][cmd_name] = {'msg': msg, 'time': now}
    
    await update.message.reply_text(f"✨ {msg}", parse_mode=ParseMode.HTML)

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    
    mapping = {
        "gay": (["🌈 Today's gay is {user}! ({pct}% gay) 🌚", "🦄 {user} is fabulous! {pct}% 🏳️‍🌈"], True),
        "roast": (["💀 {user} got roasted! 🔥", "🗑️ {user} is the group trash! 🏊‍♂️💀"], False),
        "simp": (["💘 {user} is the SIMP! 😍📈", "🐕 {user} is barking! 🦴💦"], False),
        "legend": (["👑 {user} is THE LEGEND! 😎⚡", "🌟 {user} is the ICON! 👑"], False),
        "noob": (["🍼 {user} is a NOOB! 😂📉", "🕹️ {user} is lagging! 🌐🐢"], False),
        "brain": (["🧠 {user}'s brain power: {pct}% 🤯🔋"], True),
        "sus": (["🤔 {user} is SUS! 🚨🕵️", "🚨 {user} = Imposter! 🤡🔪"], False),
        "foodie": (["🍔 {user} is the FOODIE! 🍕🍰"], False),
        "dance": (["🕺 {user} is DANCING! 💃🔥"], False),
        "monkey": (["🐒 {user} is a MONKEY! 🙈🍌"], False),
        "luck": (["🍀 {user}'s luck: {pct}% 🎲💸"], True),
        "sleep": (["😴 {user} is a sleepyhead! 💤"], False),
        "dead": (["💀 {user} is mentally dead! 🪦"], False),
        "cap": (["🧢 {user} is CAPPING! 🤥"], False),
        "random": (["🎲 {user} rating: {pct}%! 🤪"], True),
        "mirror": (["🪞 {user}'s mirror cracked! 💀"], False),
        "cringe": (["🤡 {user} is peak CRINGE! 🤢"], False),
        "couple": (["💞 Today's couple: {user} ❤️ {pct}% Match! 🥂"], True)
    }
    
    if cmd in mapping:
        msgs, has_pct = mapping[cmd]
        await handle_fun_command(update, cmd, msgs, has_pct)

# --- Server & Main ---
@app.route('/')
def health(): return jsonify({"status": "running"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    
    application = Application.builder().token(token).build()
    
    # TRACKER: This group=-1 handler ensures the bot sees everyone who talks
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_members), group=-1)
    
    fun_list = ["gay", "roast", "simp", "legend", "noob", "brain", "sus", "foodie", "dance", "monkey", "luck", "sleep", "dead", "cap", "random", "mirror", "cringe", "couple", "start"]
    for cmd in fun_list:
        if cmd == "start":
            application.add_handler(CommandHandler(cmd, lambda u, c: u.message.reply_text("Bot Active! 🚀")))
        else:
            application.add_handler(CommandHandler(cmd, cmd_dispatcher))

    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
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
