import os
import logging
import random
import threading
from datetime import datetime, time, timedelta
from threading import Thread
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Data Storage ---
# daily_locks structure: { chat_id: { 'date': date, 'commands': {cmd: data}, 'used_users': {user_id} } }
daily_locks = {}
# active_chats: A set of chat IDs where the bot is used
active_chats = set()
lock_mutex = threading.Lock()

# --- Expanded Message Pools ---

MORNING_MSGS = [
    "🌅 **Good Morning!** May your coffee be strong and your day be legendary! ☕✨",
    "☀️ **Rise and Shine!** A new day to roast and be roasted! 🚀🔥",
    "🌻 **Morning, Legends!** Wake up and start simping for life! 😜🌈",
    "🌈 **New Day Alert!** Sending positive vibes and chaos to the group! 🤗✨"
]

NIGHT_MSGS = [
    "🌙 **Good Night!** Time to recharge those 2 active brain cells! 🧠🔋",
    "😴 **Sweet Dreams!** Don't let the cringe follow you to bed! ⚰️💀",
    "🌌 **Sleep Tight!** See you tomorrow for more madness! ✨💤",
    "🛌 **Lights Out!** Off to dreamland, you absolute legends! 🌙🛸"
]

ROASTS = [
    "💀 {user} got roasted harder than a marshmallow! 🔥🍗",
    "🗑️ {user} is the reason the gene pool needs a lifeguard! 🏊‍♂️💀",
    "🤏 {user}'s contribution here is like a 0% discount! 📉🤣",
    "🦴 Someone give {user} a bone, they're starving for attention! 🐶🦴",
    "🤡 {user} just dropped their brain. Oh wait, they never had one! 🧠🚫"
]

GAY_MESSAGES = [
    "🌈 Today's gay is {user}! ({pct}% gay) 🌚✨",
    "🦄 {user} is feeling fabulous today! {pct}% 🏳️‍🌈💅",
    "✨ The rainbow chose {user} at {pct}% power! 🌈💫",
    "💅 {user} is slaaaying the group today! 👑🏳️‍🌈"
]

# --- Logic & Helpers ---

def get_ist_time():
    """Returns current time in IST"""
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def reset_and_track(chat_id):
    """Resets daily data and tracks the chat ID for scheduled messages"""
    today = get_ist_time().date()
    with lock_mutex:
        active_chats.add(chat_id)
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {
                'date': today,
                'commands': {},
                'used_users': set()
            }

async def get_unique_random_member(update: Update, chat_id):
    try:
        admins = await update.effective_chat.get_administrators()
        human_members = [admin.user for admin in admins if not admin.user.is_bot]
        
        # Filter: Exclude users already used today in THIS group
        available = [u for u in human_members if u.id not in daily_locks[chat_id]['used_users']]
        
        if not available:
            # If everyone is used, reuse from human members
            return random.choice(human_members) if human_members else update.effective_user
            
        return random.choice(available)
    except Exception:
        return update.effective_user

def get_user_display(user):
    if not user: return "Unknown Entity 👤"
    return f"@{user.username}" if user.username else user.first_name

# --- Scheduled Job Functions ---

async def send_global_morning(context: ContextTypes.DEFAULT_TYPE):
    msg = random.choice(MORNING_MSGS)
    for chat_id in list(active_chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
        except Exception as e:
            logger.warning(f"Could not send morning message to {chat_id}: {e}")

async def send_global_night(context: ContextTypes.DEFAULT_TYPE):
    msg = random.choice(NIGHT_MSGS)
    for chat_id in list(active_chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
        except Exception as e:
            logger.warning(f"Could not send night message to {chat_id}: {e}")

# --- Handlers ---

async def handle_fun_command(update: Update, cmd_name, messages_list, has_pct=False):
    chat_id = update.effective_chat.id
    reset_and_track(chat_id)
    
    with lock_mutex:
        locked_cmd = daily_locks[chat_id]['commands'].get(cmd_name)

    if locked_cmd:
        msg = locked_cmd['message']
        await update.message.reply_text(f"📌 **Daily Record:**\n{msg}", parse_mode='Markdown')
    else:
        user = await get_unique_random_member(update, chat_id)
        user_display = get_user_display(user)
        pct = random.randint(0, 100) if has_pct else None
        
        raw_msg = random.choice(messages_list)
        formatted_msg = raw_msg.format(user=user_display, pct=pct)
        
        with lock_mutex:
            daily_locks[chat_id]['commands'][cmd_name] = {'user': user, 'message': formatted_msg}
            daily_locks[chat_id]['used_users'].add(user.id)
            
        await update.message.reply_text(f"✨ {formatted_msg}")

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0].replace('/', '').split('@')[0].lower()
    mapping = {
        "roast": (ROASTS, False), "gay": (GAY_MESSAGES, True),
        # Add all your other commands (simp, legend, etc.) here
    }
    if cmd in mapping:
        msgs, has_pct = mapping[cmd]
        await handle_fun_command(update, cmd, msgs, has_pct)

# --- Server & Main ---

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(token).build()
    job_queue = application.job_queue

    # Schedule Jobs (Times converted to UTC for servers)
    job_queue.run_daily(send_global_morning, time=time(hour=1, minute=30))  # 7 AM IST
    job_queue.run_daily(send_global_night, time=time(hour=17, minute=30))  # 11 PM IST

    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Active! 🚀 Type /commands to begin!")))
    
    # Register all fun commands
    fun_list = ["roast", "gay", "simp", "legend", "noob", "brain", "sleep", "foodie", "dead", "monkey", "cap", "sus", "random", "mirror", "dance"]
    for cmd in fun_list:
        application.add_handler(CommandHandler(cmd, cmd_dispatcher))

    application.run_polling()

if __name__ == '__main__':
    main()
