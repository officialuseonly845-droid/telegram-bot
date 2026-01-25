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
# daily_locks structure: { chat_id: { 'date': date, 'commands': {}, 'user_strikes': {}, 'seen_users': {} } }
daily_locks = {}
lock_mutex = threading.Lock()

# --- Helpers ---
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def safe_h(text):
    return html.escape(text or "Unknown Entity")

def init_chat_data(chat_id):
    today = get_ist_time().date()
    with lock_mutex:
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {
                'date': today,
                'commands': {},
                'user_strikes': {}, # {user_id: count}
                'seen_users': {}
            }

async def get_target_member(update: Update, chat_id, count=1):
    data = daily_locks[chat_id]
    
    # Pool: Seen Users + Admins
    candidates = {uid: u for uid, u in data['seen_users'].items()}
    try:
        admins = await update.effective_chat.get_administrators()
        for a in admins:
            if not a.user.is_bot: candidates[a.user.id] = a.user
    except: pass

    # STRIKE RULE: Filter users who have been picked < 2 times today
    available_ids = [uid for uid in candidates.keys() if data['user_strikes'].get(uid, 0) < 2]

    # Safety: Reset strikes if pool is exhausted
    if len(available_ids) < count:
        data['user_strikes'] = {}
        available_ids = list(candidates.keys())

    if not available_ids: return [update.effective_user] * count
    
    chosen_ids = random.sample(available_ids, min(count, len(available_ids)))
    
    # Increment strikes
    for cid in chosen_ids:
        data['user_strikes'][cid] = data['user_strikes'].get(cid, 0) + 1
        
    return [candidates[cid] for cid in chosen_ids]

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
    if locked_cmd:
        await update.message.reply_text(f"📌 <b>Daily Record:</b>\n{locked_cmd['msg']}", parse_mode=ParseMode.HTML)
        return

    if cmd_name == "chammar":
        u_disp = "<b>Shakti</b>"
        pct = random.randint(1, 100)
        msg = random.choice(messages_list).format(user=u_disp, pct=pct)
    elif cmd_name == "couple":
        users = await get_target_member(update, chat_id, count=2)
        u1 = f"<b>{safe_h(users[0].username or users[0].first_name)}</b>"
        u2 = f"<b>{safe_h(users[1].username or users[1].first_name)}</b>"
        pct = random.randint(1, 100)
        msg = random.choice(messages_list).format(u1=u1, u2=u2, pct=pct)
    else:
        user = (await get_target_member(update, chat_id))[0]
        u_disp = f"<b>{safe_h(user.username or user.first_name)}</b>"
        pct = random.randint(0, 100)
        msg = random.choice(messages_list).format(user=u_disp, pct=pct)

    daily_locks[chat_id]['commands'][cmd_name] = {'msg': msg, 'time': now}
    await update.message.reply_text(f"✨ {msg}", parse_mode=ParseMode.HTML)

async def cmd_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    
    mapping = {
        "chammar": ([
            "🚽 <b>Shakti</b> detected! The Harpic CEO is here to scrub our souls! 🧴🤡",
            "🧹 <b>Shakti</b> doesn't have a future, he just has a longer mop handle! 😂🧴",
            "🧴 <b>Shakti</b>'s birth certificate is actually a Harpic receipt! 🧼🤣",
            "🤡 <b>Shakti</b>'s only talent is making the toilet seat shine! 🚽👑",
            "🧼 Breaking: <b>Shakti</b> tried to think, but his brain was a scrub pad! 🧹🏆",
            "💦 <b>Shakti</b> is the only guy who uses a mop as a selfie stick! 🧴💀",
            "🧹 If scrubbing toilets paid in gold, <b>Shakti</b> would still be a beggar! 🥇😂",
            "🚽 <b>Shakti</b> drinks Harpic to keep his thoughts from smelling! 🧹💞",
            "🧴 <b>Shakti</b> is {pct}% finished with the public toilets. Work harder! 🤡💦",
            "🧼 <b>Shakti</b> is the reason why Harpic sales are up and IQ is down! 🧹🧼",
            "🪣 <b>Shakti</b>'s family tree is just a line of janitors with buckets! 🤡🚽",
            "🧼 <b>Shakti</b>, why are you here? Did the toilet stop clogging? 🧹🤣",
            "🚽 <b>Shakti</b> has {pct}% Harpic in his blood. Chemical waste! 🧴💀",
            "🧹 <b>Shakti</b>'s mop has a higher IQ than him! ({pct}%) 🧠🤡",
            "🧴 <b>Shakti</b> is {pct}% professional cleaner, 100% failure! 🧼📉",
            "🪠 <b>Shakti</b> is the King of Commode, Sultan of Sewage! 👑🚽",
            "💦 <b>Shakti</b>'s only contribution to society is a clean urinal! 🧹🚮",
            "🧴 <b>Shakti</b>, stop texting and scrub. The Harpic is drying! 🧴💨",
            "🧹 <b>Shakti</b> is {pct}% done with his shift. Get back in the stall! 🚽🏃‍♂️",
            "🤡 <b>Shakti</b> is the only man whose dreams are flushed every morning! 🚽🌊"
        ], True),
        "gay": ([
            "🌈 Today's gay is {user}! ({pct}% gay) 🌚✨", "🦄 {user} is fabulous! {pct}% 🏳️‍🌈💅",
            "🌈 {user} just dropped their heterosexuality! {pct}% 📉", "🍭 {user} is {pct}% rainbow-coded! 🌈⚡",
            "💅 Slay {user}! You are {pct}% an icon! ✨🏳️‍🌈", "🌈 The radar found {user}! Result: {pct}% 📡",
            "✨ {user} is {pct}% glitter and rainbows! 🦄🌈", "🔥 {user} is burning with {pct}% pride! 🏳️‍🌈✨",
            "💅 {user} is {pct}% more fabulous than you! 👑", "🌈 {user} is the group's official rainbow! {pct}% 🎨"
        ], True),
        "roast": ([
            "💀 {user} is the reason the gene pool needs a lifeguard! 🏊‍♂️", "🗑️ {user} looked in the mirror and it asked for therapy! 😭",
            "🦴 Someone give {user} a bone, they're starving for attention! 🦴", "🤡 {user} dropped their brain. Oh wait, they never had one! 🚫",
            "🔥 {user} got roasted harder than a cheap marshmallow! 🍗", "🚑 Call 911! {user} just got destroyed! 💨",
            "🗑️ {user} is human trash, but even trash gets picked up! 🚮", "🤏 {user}'s contribution is like a 0% discount! 📉",
            "🦷 {user} is so ugly, the doctor slapped their mom! 🤱", "🧟 {user} could survive a zombie apocalypse! 🧠"
        ], False),
        "noob": ([
            "🍼 {user} is today's official group NOOB! 😂📉", "🕹️ {user} is lagging in real life! 🌐🐢",
            "🐣 {user} is still in beginner mode! 🍼🎮", "🧱 {user} just failed the easiest tutorial! 🚧",
            "🐢 Speed of {user}: Error 404 - Not Found! 📉", "🍼 {user} needs a diaper change after that play! 👶",
            "🧸 {user} still plays with blocks! 🧱😂", "🎮 {user} is the reason teams lose! 📉🚫",
            "🍼 {user} = Professional Tutorial Skipper! 👶", "😅 {user} is a level 0 boss! 👾📉"
        ], False),
        "aura": ([
            "✨ {user}'s aura today: {pct}% (Absolute Boss!) 👑🎖️", "📉 {user}'s aura: -{pct} (Bro is cooked) 💀",
            "🌟 {user} is glowing with {pct}% main character energy! 🌌", "🌑 {user} has the aura of a wet cardboard box. ({pct}%) 📦",
            "💎 {user} has {pct}% diamond aura! ✨💎", "🦾 {user} aura level: {pct}% Chad! 🗿🦾",
            "🧿 {user} is radiating {pct}% spiritual energy! 🔮", "💨 {user}'s aura just evaporated... {pct}% left! 🌬️",
            "🔥 {user} has {pct}% legendary aura! ⚔️🛡️", "🌈 {user} has {pct}% colorful aura! 🎨✨"
        ], True),
        "horny": ([
            "🚨 {user} horny level: {pct}% (BONK!) 🚔⚖️", "🥵 {user} is thirsty! {pct}% thirst detected! 💧",
            "🚔 Calling the Horny Police for {user}! Level: {pct}% 👮‍♂️", "🧊 {user} needs a cold shower! {pct}% hot! 🚿❄️",
            "😈 {user} has pure demon energy today! {pct}% 🍷", "🧿 {user} is surprisingly calm. Only {pct}% thirsty! 😇",
            "🥵 {user} is {pct}% down bad! 📉🚔", "🔥 {user} is vibrating at {pct}% horny frequency! ⚡",
            "👮 {user} is on the most-wanted horny list! {pct}% 📝", "🤤 {user} is drooling over the chat! {pct}% 💦"
        ], True),
        "brain": ([
            "🧠 {user}'s brain cells active: {pct}% (Running on fumes) 🔬", "💡 {user} has a lightbulb moment... at {pct}% brightness! 🕯️",
            "💭 {user}'s IQ today: {pct}% (A potato has more) 🥔", "🤖 {user} is processing at {pct}% efficiency! ⚙️",
            "🌪️ {user}'s head is empty, just wind blowing. ({pct}%) 💨", "🧬 {user} is currently using {pct}% of their power! 🤯",
            "🧠 {user} has {pct}% of a brain left! 📉💀", "📡 {user} is searching for a signal... {pct}% found! 📡",
            "🧮 {user} can't even count to {pct}! 🔢😂", "🔋 {user}'s brain is at {pct}% battery! 🔌"
        ], True),
        "monkey": ([
            "🐒 {user} is the group MONKEY! 🙈🍌", "🐵 {user} needs a zoo immediately! 😂🙊",
            "🐒 {user} is going APE in the chat! 🦍🔥", "🍌 {user} is the official Banana Lover! 🐵",
            "🙊 {user} is speaking Monkey language! 🐒💬", "🌴 {user} just escaped the jungle! 🏃‍♂️",
            "🐒 {user} is {pct}% chimpanzee today! 🐒", "🙉 {user} hears no evil, but acts like it! 🙊",
            "🍌 Keep {user} away from the fruit basket! 🐵", "🦍 {user} is the King of the Jungle! 👑🌴"
        ], False),
        "couple": ([
            "💞 Today's couple: {u1} ❤️ {u2} ({pct}% match!) 🏩", "💍 I hear wedding bells for {u1} and {u2}! ({pct}%) 🔔",
            "🔥 {u1} ❤️ {u2} = Hottest Pair! ({pct}% fire) 🌶️", "💔 {u1} and {u2} have {pct}% chemistry. Stay friends! 🫂",
            "🏩 {u1} and {u2} need a room! ({pct}% spicy) 🔞", "✨ Destined by the bot: {u1} ❤️ {u2}! ({pct}%) 🌌",
            "🧸 {u1} and {u2} are a cute match! ({pct}%) 🍭", "🥊 {u1} and {u2} are a match made in a boxing ring! ({pct}%) 🥊",
            "🍭 {u1} and {u2} are {pct}% sweet together! 🍬", "🚢 I'm shipping {u1} and {u2}! ({pct}% match) ⚓"
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_members), group=-1)
    
    fun_list = ["chammar", "gay", "roast", "noob", "aura", "horny", "brain", "monkey", "couple", "start"]
    for cmd in fun_list:
        if cmd == "start":
            application.add_handler(CommandHandler(cmd, lambda u, c: u.message.reply_text("Bot Active! 🚀")))
        else:
            application.add_handler(CommandHandler(cmd, cmd_dispatcher))

    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
