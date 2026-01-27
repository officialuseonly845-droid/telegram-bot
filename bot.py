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

# --- Persistent Data ---
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

# --- AI Engine (10-Model Failover & 15s Timeout) ---
async def get_ai_response(user_text):
    if not OPENROUTER_KEY: return "⚠️ API Key missing!"
    
    models_to_try = [
        "google/gemini-2.0-flash-exp:free", "google/gemma-3-27b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free", "arcee-ai/trinity-mini:free",
        "z-ai/glm-4.5-air:free", "openai/gpt-oss-20b:free",
        "tngtech/deepseek-r1t-chimera:free", "tngtech/tng-r1t-chimera:free",
        "deepseek/deepseek-r1-0528:free", "deepseek/deepseek-r1:free"
    ]
    
    for model in models_to_try:
        try:
            timeout_cfg = httpx.Timeout(15.0, connect=3.0) 
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "HTTP-Referer": "https://stackhost.org", "X-Title": "Beluga Bot"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": f"You are Beluga, a witty bot. Answer in 1 short sentence."},
                            {"role": "user", "content": user_text}
                        ]
                    }
                )
                if res.status_code == 200:
                    return res.json()['choices'][0]['message']['content']
        except: continue
    return "All brain cells busy. Try again later! 💤"

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

    if WAKE_WORD in text or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
        await context.bot.send_chat_action(chat_id, "typing")
        reply = await get_ai_response(text)
        await update.message.reply_text(reply)

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
            "🚽 <b>Shakti</b>'s kingdom is the toilet! 👑", "🧴 {pct}% finished. Work harder, <b>Shakti</b>! 🤡"
        ], True),
        "gay": ([
            "🌈 Today's gay is {user}! ({pct}%) 🌚", "🦄 {user} is fabulous! {pct}% 💅", "🏳️‍🌈 {user} is {pct}% rainbow-coded!",
            "💅 Slay {user}! {pct}% an icon!", "🌈 Radar found {user}: {pct}%!", "✨ {user} is {pct}% glitter!",
            "🔥 {user} is {pct}% pride!", "👑 {user} is {pct}% fabulous!", "🎨 {user} is the rainbow! {pct}%",
            "🌈 {user} dropped heterosexuality! {pct}%"
        ], True),
        "roast": ([
            "💀 {user} is the reason the gene pool needs a lifeguard!", "🗑️ Mirror asked {user} for therapy!",
            "🦴 {user} starving for attention!", "🤡 {user} dropped their brain!", "🔥 {user} roasted like a marshmallow!",
            "🚑 {user} destroyed!", "🚮 {user} is human trash!", "🤏 {user}'s contribution: 0%!",
            "🦷 {user} so ugly, the doctor slapped their mom!", "🧟 Zombies won't eat {user}... no brains!"
        ], False),
        "aura": ([
            "✨ {user}'s aura: {pct}% (Boss!) 👑", "📉 -{pct} Aura! (Cooked) 💀", "🌟 {user} glowing! {pct}%! 🌌",
            "🌑 Cardboard aura: {pct}% 📦", "💎 {user} has {pct}% diamond aura!", "🗿 {user} aura: {pct}% Chad!",
            "🧿 {user} radiating {pct}% energy!", "🌬️ Aura evaporated! {pct}% left!", "⚔️ {user} legendary aura: {pct}%!",
            "🎨 {user} colorful aura: {pct}%!"
        ], True),
        "horny": ([
            "🚨 {user} horny level: {pct}% (BONK!) 🚔", "🥵 {user} thirsty! {pct}% 💧", "👮 Calling Horny Police! {pct}%",
            "❄️ {user} needs a cold shower! {pct}%", "🍷 {user} demon energy: {pct}%", "😇 {user} is calm. {pct}% thirsty.",
            "📉 {user} is {pct}% down bad!", "⚡ {user} vibrating at {pct}%!", "📝 {user} is on the wanted list! {pct}%",
            "💦 {user} is drooling! {pct}%"
        ], True),
        "brain": ([
            "🧠 {user}'s brain cells: {pct}% 🔋", "💡 Lightbulb: {pct}% brightness!", "🥔 IQ: {pct}% (Potato) 🥔",
            "⚙️ Processing at {pct}%!", "🌪️ Head is empty! ({pct}%)", "🤯 Using {pct}% of power!",
            "📉 {user} has {pct}% brain left!", "📡 Searching for signal... {pct}%!", "🔢 {user} can't count to {pct}!",
            "🔌 Brain battery: {pct}%!"
        ], True),
        "monkey": ([
            "🐒 {user} is the group MONKEY! 🙈", "🍌 {user} Banana Lover! 🐵", "🐒 {user} is {pct}% chimpanzee!",
            "🌴 {user} escaped the jungle!", "🙊 {user} speaking Monkey language!", "🦍 {user} is the King!",
            "🐒 {user} is going APE!", "🍌 Don't give {user} a banana!", "🐒 {user} climbing trees now!",
            "🌴 Jungle ID found for {user}!"
        ], False),
        "couple": ([
            "💞 Couple: {u1} ❤️ {u2} ({pct}% match!) 🏩", "💍 Wedding bells: {u1} & {u2}! ({pct}%) 🔔",
            "🔥 {u1} ❤️ {u2} = Hottest Pair! ({pct}% fire)", "💔 {u1} & {u2}: {pct}% chemistry.",
            "🏩 {u1} & {u2} need a room! ({pct}% spicy)", "✨ Destined: {u1} ❤️ {u2}! ({pct}%)",
            "🍭 {u1} & {u2} are a match! ({pct}%)", "🥊 {u1} & {u2} in a boxing ring!",
            "🍬 {u1} & {u2} sweet together! ({pct}%)", "🚢 Shipping {u1} & {u2}! ({pct}%)"
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

# --- Server ---
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

