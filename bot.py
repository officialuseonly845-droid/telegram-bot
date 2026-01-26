import os
import logging
import random
import threading
import html
import httpx
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

# --- Config ---
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
AI_MODEL = "google/gemini-2.0-flash-exp:free"
WAKE_WORD = "beluga"

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

async def get_ai_response(user_text):
    if not OPENROUTER_KEY: return "AI Error: Key missing."
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                json={
                    "model": AI_MODEL,
                    "messages": [
                        {"role": "system", "content": f"You are Beluga, a witty and sharp Telegram bot. Only answer if your name '{WAKE_WORD}' is taken. Be concise and funny."},
                        {"role": "user", "content": user_text}
                    ]
                },
                timeout=15.0
            )
            return res.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "Beluga is sleeping right now. Try later! 💤"

async def get_target_member(update: Update, chat_id, count=1):
    data = daily_locks[chat_id]
    candidates = {uid: u for uid, u in data['seen_users'].items()}
    try:
        admins = await update.effective_chat.get_administrators()
        for a in admins:
            if not a.user.is_bot: candidates[a.user.id] = a.user
    except: pass

    available_ids = [uid for uid in candidates.keys() if data['user_strikes'].get(uid, 0) < 2]
    if len(available_ids) < count:
        data['user_strikes'] = {}
        available_ids = list(candidates.keys())

    if not available_ids: return [update.effective_user] * count
    chosen_ids = random.sample(available_ids, min(count, len(available_ids)))
    for cid in chosen_ids:
        data['user_strikes'][cid] = data['user_strikes'].get(cid, 0) + 1
    return [candidates[cid] for cid in chosen_ids]

# --- Core Message Handler (Greet, React, Track, AI) ---

async def core_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    user = update.effective_user
    text = update.message.text.lower() if update.message.text else ""

    # 1. Track User
    daily_locks[chat_id]['seen_users'][user.id] = user

    # 2. Greet System (Immediate response for 'hi')
    if text in ["hi", "hello", "hey", "hii", "heyy"]:
        u_name = f"<b>{safe_h(user.first_name)}</b>"
        replies = [f"Hello {u_name}, how are you? 😊", f"Hey {u_name}! ✨", f"Hi {u_name}! 👋", f"Hello {u_name}, nice to see you! 🌟", f"Hey there {u_name}! 🙌", f"Hi {u_name}, glad you're here! 🎈", f"Hello {u_name}, staying hydrated? 💧"]
        return await update.message.reply_text(random.choice(replies), parse_mode=ParseMode.HTML)

    # 3. React (Every 6th message)
    with lock_mutex:
        chat_counters[chat_id] += 1
        count = chat_counters[chat_id]
    if count % 6 == 0:
        try: await update.message.set_reaction(reaction=random.choice(["👍", "🔥", "😂", "❤️"]))
        except: pass

    # 4. AI Activation Logic (Only if 'beluga' is mentioned or it's a reply)
    is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
    if WAKE_WORD in text or is_reply_to_bot:
        await context.bot.send_chat_action(chat_id, "typing")
        reply = await get_ai_response(text)
        await update.message.reply_text(reply)

# --- Fun Command Handler ---

async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    
    if cmd in daily_locks[chat_id]['commands']:
        return await update.message.reply_text(f"📌 <b>Daily Record:</b>\n{daily_locks[chat_id]['commands'][cmd]['msg']}", parse_mode=ParseMode.HTML)

    mapping = {
        "chammar": ([
            "🚽 <b>Shakti</b> detected! Harpic CEO is here! 🧴🤡", "🧹 <b>Shakti</b> won 'Mop Man of the Year'! 🏆",
            "🧴 <b>Shakti</b>'s favorite perfume? Harpic Blue! 🧼", "🤡 <b>Shakti</b>'s dreams are flushed every morning! 🚽🌊",
            "🧼 <b>Shakti</b> drinks Harpic to stay clean! 💦", "🧹 Olympic Golden Mop winner: <b>Shakti</b>! 🥇",
            "🚽 <b>Shakti</b> + Mop = Love Story! 🧹💞", "🧴 <b>Shakti</b>: {pct}% pro cleaner, 100% failure! 📉",
            "🪠 <b>Shakti</b>, Sultan of Sewage! 👑🚽", "💦 <b>Shakti</b>'s contribution: a clean urinal! 🧹",
            "🪣 <b>Shakti</b>'s family tree is just buckets! 🤡", "🧼 Toilet clogged again, <b>Shakti</b>? 🧹🤣",
            "🚽 <b>Shakti</b> is {pct}% Harpic! 🧴💀", "🧹 <b>Shakti</b>'s mop is smarter! ({pct}%) 🧠",
            "🧴 Scrub, <b>Shakti</b>! Harpic is drying! 💨", "🧹 {pct}% shift done. Back to the stall, <b>Shakti</b>! 🏃‍♂️",
            "🧼 <b>Shakti</b>'s ID is a Harpic receipt! 🧼", "🤡 Sales are up because of <b>Shakti</b>! 🧴",
            "🚽 <b>Shakti</b>'s kingdom is the public toilet! 👑", "🧴 {pct}% finished. Work harder, <b>Shakti</b>! 🤡"
        ], True),
        "gay": ([
            "🌈 Today's gay is {user}! ({pct}%) 🌚", "🦄 {user} is fabulous! {pct}% 🏳️‍🌈💅",
            "🌈 {user} is {pct}% rainbow-coded! ⚡", "💅 Slay {user}! {pct}% an icon! ✨",
            "🌈 Radar found {user}: {pct}% 📡", "✨ {user} is {pct}% glitter! 🌈",
            "🔥 {user} is {pct}% pride! 🏳️‍🌈", "💅 {user} is {pct}% fabulous! 👑",
            "🌈 {user} is the rainbow! {pct}% 🎨", "🌈 {user} dropped heterosexuality! {pct}% 📉"
        ], True),
        "roast": ([
            "💀 {user} is the reason the gene pool needs a lifeguard! 🏊‍♂️", "🗑️ Mirror asked {user} for therapy! 😭",
            "🦴 {user} is starving for attention! 🦴", "🤡 {user} dropped their brain! 🚫",
            "🔥 {user} roasted like a marshmallow! 🍗", "🚑 {user} destroyed! 💨",
            "🚮 {user} is human trash! 🚮", "🤏 {user}'s contribution: 0%! 📉",
            "🦷 {user} is so ugly, the doctor slapped their mom! 🤱", "🧟 Zombies won't eat {user}... no brains! 🧠"
        ], False),
        "aura": ([
            "✨ {user}'s aura: {pct}% (Boss!) 👑", "📉 {user}'s aura: -{pct} (Cooked) 💀",
            "🌟 {user} is glowing! {pct}% Main Character! 🌌", "🌑 {user} has aura of wet cardboard box. ({pct}%) 📦",
            "💎 {user} has {pct}% diamond aura! ✨", "🦾 {user} aura: {pct}% Chad! 🗿",
            "🧿 {user} radiating {pct}% energy! 🔮", "💨 {user}'s aura evaporated! {pct}% left! 🌬️",
            "🔥 {user} has {pct}% legendary aura! ⚔️", "🌈 {user} has {pct}% colorful aura! 🎨"
        ], True),
        "horny": ([
            "🚨 {user} horny level: {pct}% (BONK!) 🚔", "🥵 {user} is {pct}% thirsty! 💧",
            "👮 Calling Horny Police for {user}! {pct}% 👮‍♂️", "🧊 {user} needs a cold shower! {pct}% ❄️",
            "😈 {user} has demon energy! {pct}% 🍷", "🧿 {user} is calm. Only {pct}% thirsty! 😇",
            "🥵 {user} is {pct}% down bad! 📉", "⚡ {user} vibrating at {pct}% frequency! ⚡",
            "📝 {user} is on the most-wanted list! {pct}% 📝", "💦 {user} is drooling! {pct}% 💦"
        ], True),
        "brain": ([
            "🧠 {user}'s brain cells active: {pct}% 🔋", "💡 {user}'s lightbulb: {pct}% brightness! 🕯️",
            "🥔 {user}'s IQ today: {pct}% (Potato) 🥔", "⚙️ {user} processing at {pct}%! ⚙️",
            "💨 {user}'s head is empty! ({pct}%) 💨", "🤯 {user} using {pct}% of power! 🤯",
            "📉 {user} has {pct}% of brain left! 💀", "📡 {user} searching for signal... {pct}%! 📡",
            "🔢 {user} can't count to {pct}! 😂", "🔌 {user}'s brain battery: {pct}%! 🔌"
        ], True),
        "couple": ([
            "💞 Today's couple: {u1} ❤️ {u2} ({pct}% match!) 🏩", "💍 Wedding bells for {u1} and {u2}! ({pct}%) 🔔",
            "🔥 {u1} ❤️ {u2} = Hottest Pair! ({pct}% fire) 🌶️", "💔 {u1} and {u2}: {pct}% chemistry! 🫂",
            "🏩 {u1} and {u2} need a room! ({pct}% spicy) 🔞", "✨ Destined: {u1} ❤️ {u2}! ({pct}%) 🌌",
            "🍭 {u1} and {u2} are sweet! ({pct}%) 🍬", "🥊 {u1} and {u2} in the ring! ({pct}%) 🥊",
            "🍬 {u1} and {u2} are {pct}% sweet together! 🍬", "🚢 Shipping {u1} and {u2}! ({pct}%) ⚓"
        ], True)
    }

    if cmd in mapping:
        msgs, has_pct = mapping[cmd]
        if cmd == "chammar": 
            res = random.choice(msgs).format(user="<b>Shakti</b>", pct=random.randint(1, 100))
        elif cmd == "couple":
            m = await get_target_member(update, chat_id, 2)
            res = random.choice(msgs).format(u1=f"<b>{safe_h(m[0].first_name)}</b>", u2=f"<b>{safe_h(m[1].first_name)}</b>", pct=random.randint(1, 100))
        else:
            m = (await get_target_member(update, chat_id))[0]
            res = random.choice(msgs).format(user=f"<b>{safe_h(m.first_name)}</b>", pct=random.randint(0, 100))
        
        daily_locks[chat_id]['commands'][cmd] = {'msg': res, 'time': get_ist_time()}
        await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

# --- Server & Main ---
@app.route('/')
def health(): return jsonify({"status": "running"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_message_handler), group=-1)
    for c in ["chammar", "gay", "roast", "aura", "horny", "brain", "couple"]:
        application.add_handler(CommandHandler(c, fun_dispatcher))
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Beluga is online! 🚀")))
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
