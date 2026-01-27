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
            daily_locks[chat_id] = {'date': today, 'commands': {}, 'user_strikes': {}, 'seen_users': {}}
        if chat_id not in chat_counters:
            chat_counters[chat_id] = 0

async def get_ai_response(user_text):
    if not OPENROUTER_KEY: return "Error: API Key missing!"
    models = ["meta-llama/llama-3.3-70b-instruct:free", "meta-llama/llama-3.1-8b-instruct:free"]
    for model in models:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "HTTP-Referer": "https://stackhost.org"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": f"You are Beluga, a sharp Telegram bot. Only answer if '{WAKE_WORD}' is mentioned. Be brief."},
                            {"role": "user", "content": user_text}
                        ]
                    },
                    timeout=25.0
                )
                if res.status_code == 200:
                    return res.json()['choices'][0]['message']['content']
        except: continue
    return "Llama is resting! Try in 10s. 💤"

async def get_target_member(update: Update, chat_id, count=1):
    data = daily_locks[chat_id]
    candidates = {uid: u for uid, u in data['seen_users'].items()}
    try:
        admins = await update.effective_chat.get_administrators()
        for a in admins:
            if not a.user.is_bot: candidates[a.user.id] = a.user
    except: pass
    available = [uid for uid in candidates.keys() if data['user_strikes'].get(uid, 0) < 2]
    if len(available) < count:
        data['user_strikes'] = {}; available = list(candidates.keys())
    chosen = random.sample(available, min(count, len(available)))
    for cid in chosen: data['user_strikes'][cid] = data['user_strikes'].get(cid, 0) + 1
    return [candidates[cid] for cid in chosen]

# --- Core Handlers ---

async def core_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    text = update.message.text.lower() if update.message.text else ""
    daily_locks[chat_id]['seen_users'][update.effective_user.id] = update.effective_user

    if text in ["hi", "hello", "hey"]:
        u = f"<b>{safe_h(update.effective_user.first_name)}</b>"
        return await update.message.reply_text(f"Hi {u}! 👋", parse_mode=ParseMode.HTML)

    with lock_mutex:
        chat_counters[chat_id] += 1
        count = chat_counters[chat_id]
    if count % 6 == 0:
        try: await update.message.set_reaction(reaction=random.choice(["🔥", "😂", "❤️", "👍"]))
        except: pass

    if WAKE_WORD in text or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
        await context.bot.send_chat_action(chat_id, "typing")
        await update.message.reply_text(await get_ai_response(text))

async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    if cmd in daily_locks[chat_id]['commands']:
        return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]['msg']}", parse_mode=ParseMode.HTML)

    mapping = {
        "chammar": ([
            "🚽 <b>Shakti</b> detected! Harpic CEO is here! 🧴🤡", "🧹 <b>Shakti</b> found a new mop! 🏆",
            "🧴 <b>Shakti</b>'s perfume? Harpic Blue! 🧼", "🤡 <b>Shakti</b>'s dreams are flushed! 🌊",
            "🧼 <b>Shakti</b> drinks Harpic to stay clean! 💦", "🧹 Olympic Mop winner: <b>Shakti</b>! 🥇",
            "🚽 <b>Shakti</b> + Mop = Love Story! 💞", "🧴 <b>Shakti</b>: {pct}% pro cleaner! 📉",
            "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽", "💦 <b>Shakti</b>'s contribution: a clean urinal! 🧹",
            "🪣 <b>Shakti</b>'s family are janitors! 🤡", "🧼 Toilet clogged again, <b>Shakti</b>? 🤣",
            "🚽 <b>Shakti</b> is {pct}% Harpic! 💀", "🧹 <b>Shakti</b>'s mop is smarter! ({pct}%) 🧠",
            "🧴 Scrub, <b>Shakti</b>! Harpic is drying! 💨", "🧹 {pct}% shift done, <b>Shakti</b>! 🏃‍♂️",
            "🧼 <b>Shakti</b>'s ID is a Harpic receipt! 🧼", "🤡 Sales are up because of <b>Shakti</b>! 🧴",
            "🚽 <b>Shakti</b>'s kingdom is the toilet! 👑", "🧴 {pct}% done. Work harder, <b>Shakti</b>! 🤡"
        ], True),
        "gay": ([
            "🌈 Today's gay is {user}! ({pct}% gay) 🌚", "🦄 {user} is fabulous! {pct}% 🏳️‍🌈💅",
            "🌈 {user} dropped heterosexuality! {pct}% 📉", "🍭 {user} is {pct}% rainbow-coded! ⚡",
            "💅 Slay {user}! You are {pct}% an icon! ✨", "🌈 Radar found {user}: {pct}% 📡",
            "✨ {user} is {pct}% glitter and rainbows! 🌈", "🔥 {user} is burning with {pct}% pride! 🏳️‍🌈",
            "💅 {user} is {pct}% more fabulous! 👑", "🌈 {user} is the group's official rainbow! {pct}% 🎨"
        ], True),
        "roast": ([
            "💀 {user} is the reason the gene pool needs a lifeguard! 🏊‍♂️", "🗑️ Mirror asked {user} for therapy! 😭",
            "🦴 {user} is starving for attention! 🦴", "🤡 {user} dropped their brain! 🚫",
            "🔥 {user} roasted like a marshmallow! 🍗", "🚑 {user} just got destroyed! 💨",
            "🚮 {user} is human trash! 🚮", "🤏 {user}'s contribution is like 0%! 📉",
            "🦷 {user} is so ugly, the doctor slapped their mom! 🤱", "🧟 Zombies won't eat {user}... no brains! 🧠"
        ], False),
        "aura": ([
            "✨ {user}'s aura: {pct}% (Absolute Boss!) 👑", "📉 {user}'s aura: -{pct} (Cooked) 💀",
            "🌟 {user} is glowing! {pct}% Main Character! 🌌", "🌑 {user} has aura of a wet cardboard box. ({pct}%) 📦",
            "💎 {user} has {pct}% diamond aura! ✨", "🦾 {user} aura level: {pct}% Chad! 🗿",
            "🧿 {user} is radiating {pct}% energy! 🔮", "💨 {user}'s aura evaporated! {pct}% left! 🌬️",
            "🔥 {user} has {pct}% legendary aura! ⚔️", "🌈 {user} has {pct}% colorful aura! 🎨"
        ], True),
        "horny": ([
            "🚨 {user} horny level: {pct}% (BONK!) 🚔", "🥵 {user} is thirsty! {pct}% 💧",
            "🚔 Calling Horny Police for {user}! Level: {pct}% 👮‍♂️", "🧊 {user} needs a cold shower! {pct}% ❄️",
            "😈 {user} has pure demon energy! {pct}% 🍷", "🧿 {user} is calm. Only {pct}% thirsty! 😇",
            "🥵 {user} is {pct}% down bad! 📉", "🔥 {user} vibrating at {pct}% frequency! ⚡",
            "👮 {user} is on the most-wanted list! {pct}% 📝", "🤤 {user} is drooling! {pct}% 💦"
        ], True),
        "brain": ([
            "🧠 {user}'s brain cells active: {pct}% 🔋", "💡 {user}'s lightbulb: {pct}% brightness! 🕯️",
            "🥔 {user}'s IQ today: {pct}% (Potato) 🥔", "🤖 {user} is processing at {pct}% efficiency! ⚙️",
            "🌪️ {user}'s head is empty! ({pct}%) 💨", "🧬 {user} is using {pct}% of power! 🤯",
            "🧠 {user} has {pct}% brain left! 📉", "📡 {user} searching for signal... {pct}%! 📡",
            "🧮 {user} can't count to {pct}! 😂", "🔌 {user}'s brain battery: {pct}%! 🔌"
        ], True),
        "monkey": ([
            "🐒 {user} is the group MONKEY! 🙈🍌", "🐵 {user} needs a zoo immediately! 😂🙊",
            "🐒 {user} is going APE in the chat! 🦍🔥", "🍌 {user} is the official Banana Lover! 🐵",
            "🙊 {user} is speaking Monkey language! 🐒💬", "🌴 {user} just escaped the jungle! 🏃‍♂️",
            "🐒 {user} is {pct}% chimpanzee today! 🐒", "🙉 {user} hears no evil, but acts like it! 🙊",
            "🍌 Keep {user} away from the fruit basket! 🐵", "🦍 {user} is the King of the Jungle! 👑🌴"
        ], False),
        "couple": ([
            "💞 Today's couple: {u1} ❤️ {u2} ({pct}% match!) 🏩", "💍 Wedding bells for {u1} and {u2}! ({pct}%) 🔔",
            "🔥 {u1} ❤️ {u2} = Hottest Pair! ({pct}% fire) 🌶️", "💔 {u1} and {u2}: {pct}% chemistry. Friends! 🫂",
            "🏩 {u1} and {u2} need a room! ({pct}% spicy) 🔞", "✨ Destined: {u1} ❤️ {u2}! ({pct}%) 🌌",
            "🧸 {u1} and {u2} are a cute match! ({pct}%) 🍭", "🥊 {u1} and {u2} in a boxing ring! ({pct}%) 🥊",
            "🍭 {u1} and {u2} are {pct}% sweet together! 🍬", "🚢 Shipping {u1} and {u2}! ({pct}% match) ⚓"
        ], True)
    }

    if cmd in mapping:
        msgs, _ = mapping[cmd]
        if cmd == "chammar": 
            res = random.choice(msgs).format(user="<b>Shakti</b>", pct=random.randint(1, 100))
        elif cmd == "couple":
            m = await get_target_member(update, chat_id, 2)
            res = random.choice(msgs).format(u1=f"<b>{safe_h(m[0].first_name)}</b>", u2=f"<b>{safe_h(m[1].first_name)}</b>", pct=random.randint(1, 100))
        else:
            m = (await get_target_member(update, chat_id))[0]
            res = random.choice(msgs).format(user=f"<b>{safe_h(m.first_name)}</b>", pct=random.randint(0, 100))
        daily_locks[chat_id]['commands'][cmd] = {'msg': res}
        await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

@app.route('/')
def health(): return jsonify({"status": "alive"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    bot = Application.builder().token(token).build()
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_handler), group=-1)
    for c in ["chammar", "gay", "roast", "aura", "horny", "brain", "monkey", "couple"]:
        bot.add_handler(CommandHandler(c, fun_dispatcher))
    bot.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
