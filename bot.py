import os
import logging
import random
import threading
import html
import httpx
import asyncio
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# --- Logging ---
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

# --- AI Engine (11-Model Failover & 15s Timeout) ---
async def get_ai_response(user_text):
    if not OPENROUTER_KEY: return "⚠️ API Key missing!"
    
    models_to_try = [
        "google/gemini-2.0-flash-exp:free",
        "liquid/lfm-2.5-1.2b-thinking:free",
        "google/gemma-3-27b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "arcee-ai/trinity-mini:free",
        "z-ai/glm-4.5-air:free",
        "openai/gpt-oss-20b:free",
        "tngtech/deepseek-r1t-chimera:free",
        "tngtech/tng-r1t-chimera:free",
        "deepseek/deepseek-r1-0528:free",
        "deepseek/deepseek-r1:free"
    ]
    
    for model in models_to_try:
        try:
            timeout_cfg = httpx.Timeout(15.0, connect=5.0) 
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "HTTP-Referer": "https://stackhost.org", 
                        "X-Title": "Beluga Bot Final"
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": f"You are Beluga, a sharp, witty bot. Only answer if '{WAKE_WORD}' is mentioned. Be brief."},
                            {"role": "user", "content": user_text}
                        ]
                    }
                )
                if res.status_code == 200:
                    return res.json()['choices'][0]['message']['content']
                logger.error(f"Model {model} failed: {res.status_code}")
        except: continue
    return "All 11 brain cells are tired. Check your OpenRouter dashboard! 💤"

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

# --- Handlers ---
async def core_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    text = (update.message.text or "").lower()
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

    is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
    if WAKE_WORD in text or is_reply:
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
            "🚽 <b>Shakti</b> + Mop = Love Story! 💞", "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽",
            "💦 <b>Shakti</b>'s contribution: a clean urinal! 🧹", "🧼 Toilet clogged again, <b>Shakti</b>? 🤣",
            "🚽 <b>Shakti</b> is {pct}% Harpic! 💀", "🧹 <b>Shakti</b>'s mop is smarter! ({pct}%) 🧠",
            "🧴 Scrub, <b>Shakti</b>! Harpic is drying! 💨", "🧹 {pct}% shift done, <b>Shakti</b>! 🏃‍♂️",
            "🧼 <b>Shakti</b>'s ID is a Harpic receipt! 🧼", "🤡 Sales are up because of <b>Shakti</b>! 🧴",
            "🚽 <b>Shakti</b>'s kingdom is the toilet! 👑", "🧴 {pct}% done. Work harder, <b>Shakti</b>! 🤡"
        ], True),
        "gay": ([
            "🌈 Today's gay is <b>{user_name}</b>! ({pct}%) 🌚", "🦄 <b>{user_name}</b> is fabulous! {pct}% 🏳️‍🌈💅",
            "🌈 <b>{user_name}</b> dropped heterosexuality! {pct}% 📉", "🍭 <b>{user_name}</b> is {pct}% rainbow-coded! ⚡",
            "💅 Slay <b>{user_name}</b>! You are {pct}% an icon! ✨", "🌈 Radar found <b>{user_name}</b>: {pct}% 📡",
            "✨ <b>{user_name}</b> is {pct}% glitter and rainbows! 🌈", "🔥 <b>{user_name}</b> is burning with {pct}% pride! 🏳️‍🌈",
            "💅 <b>{user_name}</b> is {pct}% fabulous! 👑", "🌈 <b>{user_name}</b> is the group rainbow! {pct}% 🎨"
        ], True),
        "roast": ([
            "💀 <b>{user_name}</b> is the reason the gene pool needs a lifeguard!", "🗑️ Mirror asked <b>{user_name}</b> for therapy! 😭",
            "🦴 <b>{user_name}</b> starving for attention! 🦴", "🤡 <b>{user_name}</b> dropped their brain! 🚫",
            "🔥 <b>{user_name}</b> roasted like a marshmallow! 🍗", "🚑 <b>{user_name}</b> destroyed! 💨",
            "🚮 <b>{user_name}</b> is human trash! 🚮", "🤏 <b>{user_name}</b>'s contribution: 0%! 📉",
            "🦷 <b>{user_name}</b> so ugly, the doctor slapped their mom! 🤱", "🧟 Zombies won't eat <b>{user_name}</b>... no brains! 🧠"
        ], False),
        "aura": ([
            "✨ <b>{user_name}</b>'s aura: {pct}% 👑", "📉 -{pct} Aura for <b>{user_name}</b>! 💀",
            "🌟 <b>{user_name}</b> glowing! {pct}%! 🌌", "🌑 <b>{user_name}</b> cardboard aura: {pct}% 📦",
            "💎 <b>{user_name}</b> has {pct}% diamond aura! ✨", "🗿 <b>{user_name}</b> aura level: {pct}% Chad! 🗿",
            "🧿 <b>{user_name}</b> radiating {pct}% energy! 🔮", "💨 <b>{user_name}</b>'s aura evaporated! {pct}%! 🌬️",
            "🔥 <b>{user_name}</b> has {pct}% legendary aura! ⚔️", "🌈 <b>{user_name}</b> has {pct}% colorful aura! 🎨"
        ], True),
        "horny": ([
            "🚨 <b>{user_name}</b> horny level: {pct}% (BONK!) 🚔", "🥵 <b>{user_name}</b> is thirsty! {pct}% 💧",
            "🚔 Calling Horny Police for <b>{user_name}</b>! Level: {pct}% 👮‍♂️", "🧊 <b>{user_name}</b> needs a cold shower! {pct}% ❄️",
            "😈 <b>{user_name}</b> has pure demon energy! {pct}% 🍷", "🧿 <b>{user_name}</b> is calm. Only {pct}% thirsty! 😇",
            "🥵 <b>{user_name}</b> is {pct}% down bad! 📉", "🔥 <b>{user_name}</b> vibrating at {pct}% frequency! ⚡",
            "👮 <b>{user_name}</b> is on the most-wanted list! {pct}% 📝", "🤤 <b>{user_name}</b> is drooling! {pct}% 💦"
        ], True),
        "brain": ([
            "🧠 <b>{user_name}</b>'s brain cells: {pct}% 🔋", "💡 <b>{user_name}</b>'s lightbulb: {pct}% brightness! 🕯️",
            "🥔 <b>{user_name}</b>'s IQ: {pct}% (Potato) 🥔", "🤖 <b>{user_name}</b> processing at {pct}%! ⚙️",
            "🌪️ <b>{user_name}</b>'s head is empty! ({pct}%) 💨", "🧬 <b>{user_name}</b> using {pct}% of power! 🤯",
            "🧠 <b>{user_name}</b> has {pct}% brain left! 📉", "📡 <b>{user_name}</b> searching for signal... {pct}%! 📡",
            "🧮 <b>{user_name}</b> can't count to {pct}! 😂", "🔌 <b>{user_name}</b>'s brain battery: {pct}%! 🔌"
        ], True),
        "monkey": ([
            "🐒 <b>{user_name}</b> is the group MONKEY! 🙈", "🍌 <b>{user_name}</b> Banana Lover! 🐵",
            "🐒 <b>{user_name}</b> is {pct}% chimpanzee! 🐒", "🌴 <b>{user_name}</b> just escaped the jungle! 🏃‍♂️",
            "🙊 <b>{user_name}</b> is speaking Monkey! 🐒💬", "🦍 <b>{user_name}</b> is the King! 👑🌴",
            "🐒 <b>{user_name}</b> is going APE! 🦍🔥", "🙉 <b>{user_name}</b> hears no evil, but acts like it! 🙊",
            "🍌 Keep <b>{user_name}</b> away from fruit! 🐵", "🐒 <b>{user_name}</b> climbing trees! 🐒"
        ], False),
        "couple": ([
            "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({pct}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! ({pct}%) 🔔",
            "🔥 <b>{u1}</b> ❤️ <b>{u2}</b> = Hottest Pair! ({pct}% fire) 🌶️", "💔 <b>{u1}</b> & <b>{u2}</b>: {pct}% chemistry. 🫂",
            "🏩 <b>{u1}</b> & <b>{u2}</b> need a room! ({pct}% spicy) 🔞", "✨ Destined: <b>{u1}</b> ❤️ <b>{u2}</b>! ({pct}%) 🌌",
            "🧸 <b>{u1}</b> & <b>{u2}</b> are a cute match! ({pct}%) 🍭", "🥊 <b>{u1}</b> & <b>{u2}</b> in a boxing ring! 🥊",
            "🍬 <b>{u1}</b> & <b>{u2}</b> sweet together! ({pct}%) 🍭", "🚢 Shipping <b>{u1}</b> & <b>{u2}</b>! ({pct}%) ⚓"
        ], True)
    }

    if cmd in mapping:
        msgs, _ = mapping[cmd]
        if cmd == "chammar": 
            res = random.choice(msgs).format(pct=random.randint(1, 100))
        elif cmd == "couple":
            m = await get_target_member(update, chat_id, 2)
            res = random.choice(msgs).format(u1=safe_h(m[0].first_name), u2=safe_h(m[1].first_name), pct=random.randint(1, 100))
        else:
            m = (await get_target_member(update, chat_id))[0]
            res = random.choice(msgs).format(user_name=safe_h(m.first_name), pct=random.randint(0, 100))
        daily_locks[chat_id]['commands'][cmd] = {'msg': res}
        await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

# --- Server & Run ---
@app.route('/')
def health(): return jsonify({"status": "running"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    bot = Application.builder().token(token).build()
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_handler), group=-1)
    for c in ["chammar", "gay", "roast", "aura", "horny", "brain", "monkey", "couple"]:
        bot.add_handler(CommandHandler(c, fun_dispatcher))
    bot.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
