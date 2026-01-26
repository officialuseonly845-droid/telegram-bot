import os
import logging
import random
import threading
import html
from datetime import datetime, timedelta
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
daily_locks = {}
chat_counters = {}
lock_mutex = threading.Lock()

# --- Helpers ---
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def safe_h(text):
    return html.escape(text or "Friend")

def init_chat_data(chat_id):
    today = get_ist_time().date()
    with lock_mutex:
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {
                'date': today,
                'commands': {},
                'user_strikes': {}, 
                'seen_users': {}
            }
        if chat_id not in chat_counters:
            chat_counters[chat_id] = 0

async def get_target_member(update: Update, chat_id, count=1):
    data = daily_locks[chat_id]
    candidates = {uid: u for uid, u in data['seen_users'].items()}
    try:
        admins = await update.effective_chat.get_administrators()
        for a in admins:
            if not a.user.is_bot: candidates[a.user.id] = a.user
    except: pass

    # STRIKE RULE: Filter users picked < 2 times today
    available_ids = [uid for uid in candidates.keys() if data['user_strikes'].get(uid, 0) < 2]

    if len(available_ids) < count:
        data['user_strikes'] = {}
        available_ids = list(candidates.keys())

    if not available_ids: return [update.effective_user] * count
    
    chosen_ids = random.sample(available_ids, min(count, len(available_ids)))
    for cid in chosen_ids:
        data['user_strikes'][cid] = data['user_strikes'].get(cid, 0) + 1
        
    return [candidates[cid] for cid in chosen_ids]

# --- Core Logic Handler (Greet, React, Track) ---

async def core_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    user = update.effective_user
    text = update.message.text.lower() if update.message.text else ""

    # 1. Track Member
    daily_locks[chat_id]['seen_users'][user.id] = user

    # 2. Greeting Logic
    if text in ["hi", "hello", "hey", "hii", "heyy"]:
        u_name = f"<b>{safe_h(user.first_name)}</b>"
        replies = [
            f"Hello {u_name}, how are you? 😊",
            f"Hey {u_name}! Hope you're having a great day! ✨",
            f"Hi {u_name}! Welcome to the chat! 👋",
            f"Hello {u_name}, nice to see you here! 🌟",
            f"Hey there {u_name}! What's up? 🙌",
            f"Hi {u_name}, glad you joined the conversation! 🎈",
            f"Hello {u_name}, staying hydrated? 💧"
        ]
        await update.message.reply_text(random.choice(replies), parse_mode=ParseMode.HTML)

    # 3. 6th Message Reaction
    with lock_mutex:
        chat_counters[chat_id] += 1
        count = chat_counters[chat_id]

    if count % 6 == 0:
        reactions = ["👍", "🔥", "😂", "❤️", "👏", "🎉", "🤩", "⚡"]
        try:
            await update.message.set_reaction(reaction=random.choice(reactions))
        except Exception: pass

# --- Fun Logic Handler ---

async def handle_fun_command(update: Update, cmd_name, messages_list, has_pct=False):
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    now = get_ist_time()
    
    locked_cmd = daily_locks[chat_id]['commands'].get(cmd_name)
    if locked_cmd:
        await update.message.reply_text(f"📌 <b>Daily Record:</b>\n{locked_cmd['msg']}", parse_mode=ParseMode.HTML)
        return

    if cmd_name == "chammar":
        u_disp = "<b>Shakti</b>"
        pct = random.randint(1, 100)
        msg = random.choice(messages_list).format(user=u_disp, pct=pct)
    elif cmd_name == "couple":
        users = await get_target_member(update, chat_id, count=2)
        u1 = f"<b>{safe_h(users[0].first_name)}</b>"; u2 = f"<b>{safe_h(users[1].first_name)}</b>"
        msg = random.choice(messages_list).format(u1=u1, u2=u2, pct=random.randint(1, 100))
    else:
        user = (await get_target_member(update, chat_id))[0]
        u_disp = f"<b>{safe_h(user.first_name)}</b>"
        msg = random.choice(messages_list).format(user=u_disp, pct=random.randint(0, 100))

    daily_locks[chat_id]['commands'][cmd_name] = {'msg': msg, 'time': now}
    await update.message.reply_text(f"✨ {msg}", parse_mode=ParseMode.HTML)

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    
    mapping = {
        "chammar": ([
            "🚽 <b>Shakti</b> detected! The Harpic CEO is here! 🧴🤡", "🧹 <b>Shakti</b> just won 'Mop Man of the Year'! 🧹🏆",
            "🧴 <b>Shakti</b>'s favorite perfume? 100% pure Harpic Blue! 🧼", "🤡 <b>Shakti</b>'s dreams are flushed every morning! 🚽🌊",
            "🧼 <b>Shakti</b> drinks Harpic to keep his 'aura' clean! 🤡💦", "🧹 If scrubbing was an Olympic sport, <b>Shakti</b> would have a Golden Mop! 🥇",
            "🚽 <b>Shakti</b> and his mop: A love story better than Twilight! 🧹💞", "🧴 <b>Shakti</b> is {pct}% professional cleaner, 100% failure! 📉",
            "🪠 <b>Shakti</b> is the King of Commode, Sultan of Sewage! 👑🚽", "💦 <b>Shakti</b>'s only contribution is a clean urinal! 🧹",
            "🪣 <b>Shakti</b>'s family tree is just janitors with buckets! 🤡", "🧼 Did the toilet stop clogging, <b>Shakti</b>? 🧹🤣",
            "🚽 <b>Shakti</b> has {pct}% Harpic in his blood! 🧴💀", "🧹 <b>Shakti</b>'s mop has a higher IQ than him! ({pct}%) 🧠",
            "🧴 <b>Shakti</b>, stop texting and scrub. The Harpic is drying! 💨", "🧹 <b>Shakti</b> is {pct}% done with his shift. Get back in the stall! 🏃‍♂️",
            "🧼 <b>Shakti</b>'s birth certificate is a Harpic receipt! 🧼", "🤡 <b>Shakti</b> is the reason Harpic sales are up! 🧴",
            "🚽 <b>Shakti</b> doesn't need a job, the public toilet is his kingdom! 👑", "🧴 <b>Shakti</b> is {pct}% finished with the toilets. Work harder! 🤡"
        ], True),
        "gay": ([
            "🌈 Today's gay is {user}! ({pct}% gay) 🌚", "🦄 {user} is fabulous! {pct}% 🏳️‍🌈💅",
            "🌈 {user} dropped their heterosexuality! {pct}% 📉", "🍭 {user} is {pct}% rainbow-coded! ⚡",
            "💅 Slay {user}! You are {pct}% an icon! ✨", "🌈 Radar found {user}! Result: {pct}% 📡",
            "✨ {user} is {pct}% glitter and rainbows! 🌈", "🔥 {user} is burning with {pct}% pride! 🏳️‍🌈",
            "💅 {user} is {pct}% more fabulous than you! 👑", "🌈 {user} is the official rainbow! {pct}% 🎨"
        ], True),
        "roast": ([
            "💀 {user} is the reason the gene pool needs a lifeguard! 🏊‍♂️", "🗑️ Mirror asked {user} for therapy! 😭",
            "🦴 {user} is starving for attention! 🦴", "🤡 {user} dropped their brain! 🚫",
            "🔥 {user} got roasted harder than a cheap marshmallow! 🍗", "🚑 {user} just got destroyed! 💨",
            "🚮 {user} is human trash! 🚮", "🤏 {user}'s contribution is 0%! 📉",
            "🦷 {user} is so ugly, the doctor slapped their mom! 🤱", "🧟 Zombies won't eat {user}... no brains! 🧠"
        ], False),
        "aura": ([
            "✨ {user}'s aura: {pct}% (Boss!) 👑", "📉 {user}'s aura: -{pct} (Cooked) 💀",
            "🌟 {user} is glowing! {pct}% Main Character! 🌌", "🌑 {user} has the aura of a wet cardboard box. ({pct}%) 📦",
            "💎 {user} has {pct}% diamond aura! ✨", "🦾 {user} aura level: {pct}% Chad! 🗿",
            "🧿 {user} radiating {pct}% spiritual energy! 🔮", "💨 {user}'s aura evaporated! {pct}% left! 🌬️",
            "🔥 {user} has {pct}% legendary aura! ⚔️", "🌈 {user} has {pct}% colorful aura! 🎨"
        ], True),
        "horny": ([
            "🚨 {user} horny level: {pct}% (BONK!) 🚔", "🥵 {user} is {pct}% thirsty! 💧",
            "👮 Calling Horny Police for {user}! Level: {pct}% 👮‍♂️", "🧊 {user} needs a cold shower! {pct}% ❄️",
            "😈 {user} has demon energy! {pct}% 🍷", "🧿 {user} is calm. Only {pct}% thirsty! 😇",
            "🥵 {user} is {pct}% down bad! 📉", "⚡ {user} vibrating at {pct}% horny frequency! ⚡",
            "📝 {user} is on the most-wanted list! {pct}% 📝", "💦 {user} is drooling! {pct}% 💦"
        ], True),
        "brain": ([
            "🧠 {user}'s brain cells active: {pct}% 🔋", "💡 {user}'s lightbulb: {pct}% brightness! 🕯️",
            "🥔 {user}'s IQ today: {pct}% (Potato) 🥔", "⚙️ {user} processing at {pct}% efficiency! ⚙️",
            "💨 {user}'s head is empty! ({pct}%) 💨", "🤯 {user} using {pct}% of power! 🤯",
            "📉 {user} has {pct}% of a brain left! 💀", "📡 {user} searching for signal... {pct}% found! 📡",
            "🔢 {user} can't count to {pct}! 😂", "🔌 {user}'s brain battery: {pct}%! 🔌"
        ], True),
        "couple": ([
            "💞 Today's couple: {u1} ❤️ {u2} ({pct}% match!) 🏩", "💍 Wedding bells for {u1} and {u2}! ({pct}%) 🔔",
            "🔥 {u1} ❤️ {u2} = Hottest Pair! ({pct}% fire) 🌶️", "💔 {u1} and {u2}: {pct}% chemistry. Stay friends! 🫂",
            "🏩 {u1} and {u2} need a room! ({pct}% spicy) 🔞", "✨ Destined: {u1} ❤️ {u2}! ({pct}%) 🌌",
            "🍭 {u1} and {u2} are sweet! ({pct}%) 🍬", "🥊 {u1} and {u2} in the boxing ring! ({pct}%) 🥊",
            "🍬 {u1} and {u2} are {pct}% sweet together! 🍬", "🚢 Shipping {u1} and {u2}! ({pct}% match) ⚓"
        ], True)
    }
    
    if cmd in mapping:
        msgs, has_pct = mapping[cmd]
        await handle_fun_command(update, cmd, msgs, has_pct)

# --- Server & Main ---
@app.route('/')
def health(): return jsonify({"status": "running"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token: return
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    application = Application.builder().token(token).build()
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_message_handler), group=-1)
    
    fun_list = ["chammar", "gay", "roast", "aura", "horny", "brain", "couple", "start"]
    for cmd in fun_list:
        if cmd == "start": application.add_handler(CommandHandler(cmd, lambda u, c: u.message.reply_text("Bot Active! 🚀")))
        else: application.add_handler(CommandHandler(cmd, cmd_dispatcher))

    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
