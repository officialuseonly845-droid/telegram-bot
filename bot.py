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

# Configure logging to see EXACTLY why it crashes in the logs
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Data Storage ---
daily_locks = {}
lock_mutex = threading.Lock()

# --- Helpers ---
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def safe_h(text):
    return html.escape(text or "Unknown Entity")

def init_chat_data(chat_id):
    with lock_mutex:
        if chat_id not in daily_locks:
            daily_locks[chat_id] = {'commands': {}, 'used_users': {}, 'seen_users': {}}

async def get_target_member(update: Update, chat_id):
    data = daily_locks[chat_id]
    now = get_ist_time()
    data['used_users'] = {uid: exp for uid, exp in data['used_users'].items() if exp > now}
    candidates = {}
    for uid, user_obj in data['seen_users'].items():
        candidates[uid] = user_obj
    try:
        admins = await update.effective_chat.get_administrators()
        for admin in admins:
            if not admin.user.is_bot: candidates[admin.user.id] = admin.user
    except: pass
    available_ids = [uid for uid in candidates.keys() if uid not in data['used_users']]
    if not available_ids:
        data['used_users'] = {}
        available_ids = list(candidates.keys())
    if not available_ids: return update.effective_user
    return candidates[random.choice(available_ids)]

# --- Handlers ---
async def track_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    daily_locks[chat_id]['seen_users'][update.effective_user.id] = update.effective_user

async def handle_fun_command(update: Update, cmd_name, messages_list, has_pct=False):
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    now = get_ist_time()
    locked_cmd = daily_locks[chat_id]['commands'].get(cmd_name)
    if locked_cmd and (now - locked_cmd['time']).days < 1:
        await update.message.reply_text(f"📌 <b>Daily Result:</b>\n{locked_cmd['msg']}", parse_mode=ParseMode.HTML)
        return
    user = await get_target_member(update, chat_id)
    u_name = safe_h(user.username) if user.username else safe_h(user.first_name)
    u_disp = f"<b>{u_name}</b>"
    pct = random.randint(0, 100) if has_pct else None
    msg = random.choice(messages_list).format(user=u_disp, pct=pct)
    daily_locks[chat_id]['used_users'][user.id] = now + timedelta(days=2)
    daily_locks[chat_id]['commands'][cmd_name] = {'msg': msg, 'time': now}
    await update.message.reply_text(f"✨ {msg}", parse_mode=ParseMode.HTML)

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    mapping = {
        "gay": (["🌈 Today's gay is {user}! ({pct}% gay) 🌚"], True),
        "roast": (["💀 {user} got roasted! 🔥"], False),
        "simp": (["💘 {user} is the SIMP! 😍"], False),
        "legend": (["👑 {user} is THE LEGEND! 😎"], False),
        "noob": (["🍼 {user} is a NOOB! 😂"], False),
        "brain": (["🧠 {user}'s brain power: {pct}% 🤯"], True),
        "sus": (["🤔 {user} is SUS! 🚨"], False),
        "luck": (["🍀 {user}'s luck: {pct}% 🎲"], True),
        "cringe": (["🤡 {user} is peak CRINGE! 🤢"], False),
        "couple": (["💞 Today's couple: {user} ❤️ {pct}% Match!"], True)
    }
    if cmd in mapping:
        msgs, has_pct = mapping[cmd]
        await handle_fun_command(update, cmd, msgs, has_pct)

@app.route('/')
def health(): return jsonify({"status": "running"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN missing!")
        return
    
    # Start Flask
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    
    # Build Application
    builder = Application.builder().token(token)
    # Important: some environments have issues with the default job queue
    application = builder.build()
    
    # Handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_members), group=-1)
    
    cmds = ["gay", "roast", "simp", "legend", "noob", "brain", "sus", "luck", "cringe", "couple"]
    for c in cmds:
        application.add_handler(CommandHandler(c, cmd_dispatcher))
    
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Active! 🚀")))

    logger.info("Bot is starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
