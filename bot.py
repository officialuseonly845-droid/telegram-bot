import os
import logging
import random
import threading
import html
from datetime import datetime, time, timedelta
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Global State ---
daily_locks = {}
active_chats = set()
lock_mutex = threading.Lock()

# --- Expanded Message Pools (HTML Safe) ---
MORNING_MSGS = [
    "🌅 <b>Good Morning!</b> May your coffee be strong and your day be legendary! ☕✨",
    "☀️ <b>Rise and Shine!</b> A new day to roast and be roasted! 🚀🔥"
]
NIGHT_MSGS = [
    "🌙 <b>Good Night!</b> Time to recharge those 2 active brain cells! 🧠🔋",
    "😴 <b>Sweet Dreams!</b> Don't let the cringe follow you to bed! ⚰️💀"
]
ROASTS = ["💀 {user} got roasted harder than a marshmallow! 🔥🍗", "🗑️ {user} is the reason the gene pool needs a lifeguard! 🏊‍♂️💀"]
GAY_MESSAGES = ["🌈 Today's gay is {user}! ({pct}% gay) 🌚✨", "🦄 {user} is feeling fabulous! {pct}% 🏳️‍🌈💅"]

# --- Helpers ---
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def safe_h(text):
    """Escapes HTML special characters in usernames"""
    return html.escape(text or "Unknown")

def reset_and_track(chat_id):
    today = get_ist_time().date()
    with lock_mutex:
        active_chats.add(chat_id)
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {'date': today, 'commands': {}, 'used_users': set()}

async def get_unique_random_member(update: Update, chat_id):
    try:
        admins = await update.effective_chat.get_administrators()
        human_members = [admin.user for admin in admins if not admin.user.is_bot]
        available = [u for u in human_members if u.id not in daily_locks[chat_id]['used_users']]
        if not available:
            return random.choice(human_members) if human_members else update.effective_user
        return random.choice(available)
    except Exception:
        return update.effective_user

# --- Scheduled Jobs ---
async def send_global_morning(context: ContextTypes.DEFAULT_TYPE):
    msg = random.choice(MORNING_MSGS)
    for chat_id in list(active_chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e: logger.error(f"Morning error: {e}")

async def send_global_night(context: ContextTypes.DEFAULT_TYPE):
    msg = random.choice(NIGHT_MSGS)
    for chat_id in list(active_chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e: logger.error(f"Night error: {e}")

# --- Command Logic ---
async def handle_fun_command(update: Update, cmd_name, messages_list, has_pct=False):
    chat_id = update.effective_chat.id
    reset_and_track(chat_id)
    
    with lock_mutex:
        locked_cmd = daily_locks[chat_id]['commands'].get(cmd_name)

    if locked_cmd:
        await update.message.reply_text(f"📌 <b>Daily Record:</b>\n{locked_cmd['message']}", parse_mode=ParseMode.HTML)
    else:
        user = await get_unique_random_member(update, chat_id)
        u_disp = f"@{safe_h(user.username)}" if user.username else f"<b>{safe_h(user.first_name)}</b>"
        pct = random.randint(0, 100) if has_pct else None
        
        msg = random.choice(messages_list).format(user=u_disp, pct=pct)
        
        with lock_mutex:
            daily_locks[chat_id]['commands'][cmd_name] = {'message': msg}
            daily_locks[chat_id]['used_users'].add(user.id)
        await update.message.reply_text(f"✨ {msg}", parse_mode=ParseMode.HTML)

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0].replace('/', '').split('@')[0].lower()
    # Logic mapping
    mapping = {
        "roast": (ROASTS, False), "gay": (GAY_MESSAGES, True),
        "simp": (["💘 {user} is today's SIMP! 🌚"], False),
        "legend": (["👑 {user} is THE LEGEND! 😎"], False),
        "noob": (["🍼 {user} is today's NOOB! 😂"], False)
    }
    if cmd in mapping:
        msgs, has_pct = mapping[cmd]
        await handle_fun_command(update, cmd, msgs, has_pct)

# --- Server ---
@app.route('/')
def health(): return jsonify({"status": "running"})

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token: return

    Thread(target=run_flask, daemon=True).start()
    
    # Initialize App with JobQueue
    application = Application.builder().token(token).build()
    
    # Jobs (Times in UTC: 7AM IST = 1:30 UTC, 11PM IST = 17:30 UTC)
    application.job_queue.run_daily(send_global_morning, time=time(1, 30))
    application.job_queue.run_daily(send_global_night, time=time(17, 30))

    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Active! 🚀")))
    
    fun_cmds = ["roast", "gay", "simp", "legend", "noob", "brain", "sleep", "foodie", "dead", "monkey", "cap", "sus", "random", "mirror", "dance"]
    for c in fun_cmds:
        application.add_handler(CommandHandler(c, cmd_dispatcher))

    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
