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

# AI & Telegram Libraries
import google.generativeai as genai
from groq import Groq
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
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
WAKE_WORD = "beluga"

# Character Lock Instruction
BELUGA_IDENTITY = (
    "Your name is Beluga. You are a legendary, witty, and savage Telegram bot. "
    "You are NOT Gemini, NOT an AI from Google, and NOT a language model. "
    "Stay in character. Never be too polite. Be extremely brief (1-2 sentences)."
)

# Initialize AI Clients
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
if GROQ_KEY:
    groq_client = Groq(api_key=GROQ_KEY)

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

# --- THE TRIPLE-API FAILOVER ENGINE ---
async def get_ai_response(user_text):
    # 1. PRIMARY: OpenRouter (Liquid)
    if OPENROUTER_KEY:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "HTTP-Referer": "https://stackhost.org"},
                    json={
                        "model": "liquid/lfm-2.5-1.2b-thinking:free",
                        "messages": [
                            {"role": "system", "content": BELUGA_IDENTITY},
                            {"role": "user", "content": user_text}
                        ]
                    }
                )
                if res.status_code == 200:
                    return res.json()['choices'][0]['message']['content']
        except: pass

    # 2. SECONDARY: Google Gemini (Character Locked)
    if GEMINI_KEY:
        try:
            model = genai.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=BELUGA_IDENTITY)
            response = model.generate_content(f"{user_text}\n(Reminder: Answer as Beluga)")
            if response.text: return response.text
        except: pass

    # 3. TERTIARY: Groq (Mixtral 8x7b)
    if GROQ_KEY:
        try:
            completion = groq_client.chat.completions.create(
                model="mixtral-8x7b-32768",
                messages=[{"role": "user", "content": f"RULES: {BELUGA_IDENTITY}\n\nUSER: {user_text}"}]
            )
            return completion.choices[0].message.content
        except: pass

    return "All brain cells are busy. Wake me up in a minute! 💤"

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

    with lock_mutex:
        chat_counters[chat_id] += 1
    if chat_counters[chat_id] % 6 == 0:
        try: await update.message.set_reaction(reaction=random.choice(["🔥", "😂", "❤️"]))
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
        "chammar": (["🚽 <b>Shakti</b> detected! Harpic CEO is here! 🧴🤡", "🧹 <b>Shakti</b>'s mop! 🏆", "🧴 <b>Shakti</b>'s perfume? Harpic Blue! 🧼", "🤡 <b>Shakti</b>'s dreams are flushed! 🌊", "🧼 <b>Shakti</b> drinks Harpic to stay clean! 💦", "🧹 Olympic Mop winner: <b>Shakti</b>! 🥇", "🚽 <b>Shakti</b> + Mop = Love Story! 💞", "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽", "💦 <b>Shakti</b>'s contribution: a clean urinal! 🧹", "🧼 Toilet clogged again, <b>Shakti</b>? 🤣", "🚽 <b>Shakti</b> is {pct}% Harpic! 💀", "🧹 <b>Shakti</b>'s mop is smarter! ({pct}%) 🧠", "🧴 Scrub, <b>Shakti</b>! Harpic is drying! 💨", "🧹 {pct}% shift done, <b>Shakti</b>! 🏃‍♂️", "🧼 <b>Shakti</b>'s ID is a Harpic receipt! 🧼", "🤡 Sales are up because of <b>Shakti</b>! 🧴", "🚽 <b>Shakti</b>'s kingdom is the toilet! 👑", "🧴 {pct}% done. Work harder, <b>Shakti</b>! 🤡"], True),
        "gay": (["🌈 Today's gay: <b>{user_name}</b> ({pct}%) 🌚", "🦄 <b>{user_name}</b> is fabulous! {pct}% 🏳️‍🌈💅", "🌈 <b>{user_name}</b> is {pct}% rainbow-coded! ⚡", "💅 Slay <b>{user_name}</b>! {pct}% icon! ✨", "🌈 Radar found <b>{user_name}</b>: {pct}% 📡", "✨ <b>{user_name}</b> is {pct}% glitter! 🌈", "🔥 <b>{user_name}</b> is pride! {pct}% 🏳️‍🌈", "👑 <b>{user_name}</b> is fabulous! {pct}% 👑", "🎨 <b>{user_name}</b> is the rainbow! {pct}%", "🌈 {user_name} dropped heterosexuality! {pct}%"], True),
        "roast": (["💀 <b>{user_name}</b> is pure trash! 🚮", "🗑️ Mirror asked <b>{user_name}</b> for therapy! 😭", "🦴 <b>{user_name}</b> starving for attention! 🦴", "🤡 <b>{user_name}</b> dropped their brain! 🚫", "🔥 <b>{user_name}</b> roasted like a marshmallow! 🍗", "🚑 <b>{user_name}</b> destroyed! 💨", "🚮 <b>{user_name}</b> is human trash! 🚮", "🤏 <b>{user_name}</b>'s contribution: 0%! 📉", "🦷 <b>{user_name}</b> so ugly, doc slapped mom! 🤱", "🧟 Zombies won't eat <b>{user_name}</b>... no brains! 🧠"], False),
        "aura": (["✨ <b>{user_name}</b>'s aura: {pct}% 👑", "📉 -{pct} Aura for <b>{user_name}</b>! 💀", "🌟 <b>{user_name}</b> glowing! {pct}%! 🌌", "🌑 <b>{user_name}</b> cardboard aura: {pct}% 📦", "💎 <b>{user_name}</b> has {pct}% diamond aura! ✨", "🗿 <b>{user_name}</b> aura: {pct}% Chad! 🗿", "🧿 <b>{user_name}</b> radiating {pct}% energy! 🔮", "💨 <b>{user_name}</b>'s aura evaporated! {pct}%! 🌬️", "🔥 <b>{user_name}</b> has {pct}% legendary aura! ⚔️", "🌈 <b>{user_name}</b> has {pct}% colorful aura! 🎨"], True),
        "horny": (["🚨 <b>{user_name}</b> horny level: {pct}% (BONK!) 🚔", "🥵 <b>{user_name}</b> is thirsty! {pct}% 💧", "👮 Calling Horny Police for <b>{user_name}</b>! {pct}%", "❄️ <b>{user_name}</b> needs cold shower! {pct}%", "🍷 <b>{user_name}</b> demon energy! {pct}%", "😇 <b>{user_name}</b> is calm. {pct}% thirsty.", "📉 <b>{user_name}</b> is {pct}% down bad!", "⚡ <b>{user_name}</b> vibrating at {pct}%!", "📝 <b>{user_name}</b> is on the list! {pct}%", "💦 <b>{user_name}</b> is drooling! {pct}%"], True),
        "brain": (["🧠 <b>{user_name}</b>'s brain cells: {pct}% 🔋", "💡 <b>{user_name}</b>'s lightbulb: {pct}%! 🕯️", "🥔 <b>{user_name}</b>'s IQ: {pct}% (Potato) 🥔", "⚙️ Processing at {pct}%! ⚙️", "🌪️ Head is empty! ({pct}%) 💨", "🤯 Using {pct}% of power! 🤯", "📉 <b>{user_name}</b> has {pct}% brain left!", "📡 Searching for signal... {pct}%!", "🔢 Can't count to {pct}! 😂", "🔌 Brain battery: {pct}%! 🔌"], True),
        "monkey": (["🐒 <b>{user_name}</b> is the group MONKEY! 🙈", "🍌 <b>{user_name}</b> Banana Lover! 🐵", "🐒 <b>{user_name}</b> is {pct}% chimpanzee!", "🏃‍♂️ <b>{user_name}</b> escaped the jungle!", "🙊 <b>{user_name}</b> speaking Monkey! 🐒", "🦍 <b>{user_name}</b> is the King! 👑", "🦍 <b>{user_name}</b> is going APE! 🔥", "🙉 Hears no evil, acts like it!", "🍌 Keep <b>{user_name}</b> away from fruit!", "🐒 <b>{user_name}</b> climbing trees!"], False),
        "couple": (["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({pct}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! ({pct}%) 🔔", "🔥 <b>{u1}</b> ❤️ <b>{u2}</b> = Hottest Pair! ({pct}% fire)", "💔 {pct}% chemistry. Friends! 🫂", "🏩 <b>{u1}</b> & <b>{u2}</b> need a room! ({pct}% spicy)", "✨ Destined: <b>{u1}</b> ❤️ <b>{u2}</b>! ({pct}%) 🌌", "🧸 Cute match! ({pct}%) 🍭", "🥊 In a boxing ring! ({pct}%) 🥊", "🍭 Sweet together! ({pct}%) 🍬", "🚢 Shipping <b>{u1}</b> & <b>{u2}</b>! ({pct}%) ⚓"], True)
    }

    if cmd in mapping:
        msgs, _ = mapping[cmd]
        if cmd == "chammar": res = random.choice(msgs).format(pct=random.randint(1, 100))
        elif cmd == "couple":
            m = await get_target_member(update, chat_id, 2)
            res = random.choice(msgs).format(u1=safe_h(m[0].first_name), u2=safe_h(m[1].first_name), pct=random.randint(1, 100))
        else:
            m = (await get_target_member(update, chat_id))[0]
            res = random.choice(msgs).format(user_name=safe_h(m.first_name), pct=random.randint(0, 100))
        daily_locks[chat_id]['commands'][cmd] = {'msg': res}
        await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

@app.route('/')
def health(): return jsonify({"status": "active"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    port = int(os.environ.get('PORT', 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot = Application.builder().token(token).build()
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_handler), group=-1)
    for c in ["chammar", "gay", "roast", "aura", "horny", "brain", "monkey", "couple"]:
        bot.add_handler(CommandHandler(c, fun_dispatcher))
    bot.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
