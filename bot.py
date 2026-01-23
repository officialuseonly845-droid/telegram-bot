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

daily_locks = {}
active_chats = set()
lock_mutex = threading.Lock()

# --- Message Pools (Ensure no raw < or > symbols exist) ---
MORNING_MSGS = [
    "🌅 <b>Good Morning!</b> May your coffee be strong and your day be legendary! ☕✨",
    "☀️ <b>Rise and Shine!</b> A new day to roast and be roasted! 🚀🔥"
]

NIGHT_MSGS = [
    "🌙 <b>Good Night!</b> Time to recharge those brain cells! 🧠🔋",
    "😴 <b>Sweet Dreams!</b> Don't let the cringe follow you to bed! ⚰️💀"
]

# --- Helpers ---
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def safe_html(text):
    """Escapes special characters to prevent 'Can't parse entities' error"""
    return html.escape(text)

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

# --- Scheduled Job Functions ---
async def send_global_morning(context: ContextTypes.DEFAULT_TYPE):
    msg = random.choice(MORNING_MSGS)
    for chat_id in list(active_chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e: logger.error(f"Error in morning: {e}")

async def send_global_night(context: ContextTypes.DEFAULT_TYPE):
    msg = random.choice(NIGHT_MSGS)
    for chat_id in list(active_chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e: logger.error(f"Error in night: {e}")

# --- Handlers ---
async def handle_fun_command(update: Update, cmd_name, messages_list, has_pct=False):
    chat_id = update.effective_chat.id
    reset_and_track(chat_id)
    
    with lock_mutex:
        locked_cmd = daily_locks[chat_id]['commands'].get(cmd_name)

    if locked_cmd:
        # Use HTML bolding instead of Markdown
        await update.message.reply_text(f"📌 <b>Daily Record:</b>\n{locked_cmd['message']}", parse_mode=ParseMode.HTML)
    else:
        user = await get_unique_random_member(update, chat_id)
        # ESCAPE the user name to prevent parse errors
        user_name = safe_html(user.first_name)
        user_display = f"@{user.username}" if user.username else user_name
        
        pct = random.randint(0, 100) if has_pct else None
        
        raw_msg = random.choice(messages_list)
        formatted_msg = raw_msg.format(user=user_display, pct=pct)
        
        with lock_mutex:
            daily_locks[chat_id]['commands'][cmd_name] = {'message': formatted_msg}
            daily_locks[chat_id]['used_users'].add(user.id)
            
        await update.message.reply_text(f"✨ {formatted_msg}", parse_mode=ParseMode.HTML)

# --- Remaining code (Main, Flask, Dispatcher) stays the same ---
# Ensure you update the Mapping to use ParseMode.HTML in the dispatcher too.
