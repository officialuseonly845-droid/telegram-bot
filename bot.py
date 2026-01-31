import os
import logging
import random
import threading
import html
import httpx
import asyncio
import traceback
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask, jsonify

import google.generativeai as genai
from groq import Groq
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# --- Logging & Initialization ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
daily_locks = {}
chat_counters = {}
lock_mutex = threading.Lock()

# --- API Configuration ---
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
WAKE_WORD = "beluga"

# Persistent Identity Instruction
BELUGA_IDENTITY = (
    "Your name is Beluga. You are a legendary, witty, and savage Telegram bot. "
    "You are NOT Gemini, NOT Meta, and NOT Google. Stay in character. "
    "Replies must be extremely brief (1-2 sentences). Be funny and sharp."
)

if GEMINI_KEY: genai.configure(api_key=GEMINI_KEY)
if GROQ_KEY: groq_client = Groq(api_key=GROQ_KEY)

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    logger.error(f"🚨 BELUGA CRASH REPORT: {''.join(tb_list)}")

# --- Helpers ---
def safe_h(text): return html.escape(text or "Friend")

def init_chat_data(chat_id):
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    with lock_mutex:
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {'date': today, 'commands': {}, 'seen_users': {}}
        if chat_id not in chat_counters: chat_counters[chat_id] = 0

# --- AI Engine (High Throughput Models) ---
async def get_ai_response(user_text):
    # 1. PRIMARY: Gemini 2.5 Flash-Lite (Highest Free Limits in India)
    if GEMINI_KEY:
        try:
            model = genai.GenerativeModel(model_name='gemini-2.5-flash-lite', system_instruction=BELUGA_IDENTITY)
            response = model.generate_content(f"{user_text}\n(Reminder: Speak as Beluga)")
            if response.text: return response.text
        except: pass

    # 2. SECONDARY: Llama 3.1 8B Instant (Groq - High Speed for Busy Groups)
    if GROQ_KEY:
        try:
            res = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": f"RULES: {BELUGA_IDENTITY}\n\nUSER: {user_text}"}]
            )
            return res.choices[0].message.content
        except: pass

    # 3. TERTIARY: Liquid LFM 2.5 (OpenRouter - Diverse Architecture)
    if OPENROUTER_KEY:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "HTTP-Referer": "https://stackhost.org"},
                    json={
                        "model": "liquid/lfm-2.5-1.2b-thinking:free",
                        "messages": [{"role": "system", "content": BELUGA_IDENTITY}, {"role": "user", "content": user_text}]
                    }
                )
                if res.status_code == 200: return res.json()['choices'][0]['message']['content']
        except: pass

    return "All my API brains are currently melting. Try again in 10s! 💤"

# --- Message Handlers ---
async def core_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    daily_locks[chat_id]['seen_users'][update.effective_user.id] = update.effective_user
    
    text = (update.message.text or "").lower()
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

    # Full Reply List (70+ Variations)
    mapping = {
        "chammar": [
            "🚽 <b>Shakti</b> detected! Harpic CEO is here! 🧴🤡", "🧹 <b>Shakti</b>'s mop! 🏆", 
            "🧴 <b>Shakti</b>'s perfume? Harpic Blue! 🧼", "🤡 <b>Shakti</b>'s dreams are flushed! 🌊", 
            "🧼 <b>Shakti</b> drinks Harpic to stay clean! 💦", "🧹 Olympic Mop winner: <b>Shakti</b>! 🥇", 
            "🚽 <b>Shakti</b> + Mop = Love Story! 💞", "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽", 
            "💦 <b>Shakti</b>'s contribution: a clean urinal! 🧹", "🧼 Toilet clogged again, <b>Shakti</b>? 🤣", 
            "🚽 <b>Shakti</b> is {pct}% Harpic! 💀", "🧹 <b>Shakti</b>'s mop is smarter! ({pct}%) 🧠", 
            "🧴 Scrub, <b>Shakti</b>! Harpic is drying! 💨", "🧹 {pct}% shift done, <b>Shakti</b>! 🏃‍♂️", 
            "🧼 <b>Shakti</b>'s ID is a Harpic receipt! 🧼", "🤡 Sales are up because of <b>Shakti</b>! 🧴", 
            "🚽 <b>Shakti</b>'s kingdom is the toilet! 👑", "🧴 {pct}% done. Work harder, <b>Shakti</b>! 🤡"
        ],
        "gay": [
            "🌈 Today's gay: <b>{user_name}</b> ({pct}%) 🌚", "🦄 <b>{user_name}</b> is fabulous! {pct}% 🏳️‍🌈💅", 
            "🌈 <b>{user_name}</b> dropped heterosexuality! {pct}% 📉", "🍭 <b>{user_name}</b> is {pct}% rainbow-coded! ⚡", 
            "💅 Slay <b>{user_name}</b>! {pct}% icon! ✨", "🌈 Radar found <b>{user_name}</b>: {pct}% 📡", 
            "✨ <b>{user_name}</b> is {pct}% glitter! 🌈", "🔥 <b>{user_name}</b> is burning with {pct}% pride! 🏳️‍🌈", 
            "👑 <b>{user_name}</b> is {pct}% fabulous! 👑", "🎨 <b>{user_name}</b> is the rainbow! {pct}%"
        ],
        "roast": [
            "💀 <b>{user_name}</b> is pure trash! 🚮", "🗑️ Mirror asked <b>{user_name}</b> for therapy! 😭", 
            "🦴 <b>{user_name}</b> starving for attention! 🦴", "🤡 <b>{user_name}</b> dropped their brain! 🚫", 
            "🔥 <b>{user_name}</b> roasted like a marshmallow! 🍗", "🚑 <b>{user_name}</b> destroyed! 💨", 
            "🚮 <b>{user_name}</b> is human trash! 🚮", "🤏 <b>{user_name}</b>'s contribution: 0%! 📉", 
            "🦷 <b>{user_name}</b> so ugly, doc slapped mom! 🤱", "🧟 Zombies won't eat <b>{user_name}</b>... no brains! 🧠"
        ],
        "aura": [
            "✨ <b>{user_name}</b>'s aura: {pct}% 👑", "📉 -{pct} Aura for <b>{user_name}</b>! 💀", 
            "🌟 <b>{user_name}</b> glowing! {pct}%! 🌌", "🌑 <b>{user_name}</b> cardboard aura: {pct}% 📦", 
            "💎 <b>{user_name}</b> has {pct}% diamond aura! ✨", "🗿 <b>{user_name}</b> aura: {pct}% Chad! 🗿", 
            "🧿 <b>{user_name}</b> radiating {pct}% energy! 🔮", "💨 <b>{user_name}</b>'s aura evaporated! {pct}%! 🌬️", 
            "🔥 <b>{user_name}</b> has {pct}% legendary aura! ⚔️", "🌈 <b>{user_name}</b> has {pct}% colorful aura! 🎨"
        ],
        "horny": [
            "🚨 <b>{user_name}</b> horny level: {pct}% (BONK!) 🚔", "🥵 <b>{user_name}</b> is thirsty! {pct}% 💧", 
            "👮 Calling Horny Police for <b>{user_name}</b>! {pct}%", "❄️ <b>{user_name}</b> needs cold shower! {pct}%", 
            "🍷 <b>{user_name}</b> demon energy! {pct}%", "😇 <b>{user_name}</b> is calm. {pct}% thirsty.", 
            "📉 <b>{user_name}</b> is {pct}% down bad!", "⚡ <b>{user_name}</b> vibrating at {pct}%!", 
            "📝 <b>{user_name}</b> is on the list! {pct}%", "💦 <b>{user_name}</b> is drooling! {pct}%"
        ],
        "brain": [
            "🧠 <b>{user_name}</b>'s brain cells: {pct}% 🔋", "💡 <b>{user_name}</b>'s lightbulb: {pct}%! 🕯️", 
            "🥔 <b>{user_name}</b>'s IQ: {pct}% (Potato) 🥔", "⚙️ Processing at {pct}%! ⚙️", 
            "🌪️ Head is empty! ({pct}%) 💨", "🤯 Using {pct}% of power! 🤯", 
            "📉 <b>{user_name}</b> has {pct}% brain left!", "📡 Searching for signal... {pct}%!", 
            "🔢 Can't count to {pct}! 😂", "🔌 Brain battery: {pct}%! 🔌"
        ],
        "monkey": [
            "🐒 <b>{user_name}</b> is the group MONKEY! 🙈", "🍌 <b>{user_name}</b> Banana Lover! 🐵", 
            "🐒 <b>{user_name}</b> is {pct}% chimpanzee!", "🏃‍♂️ <b>{user_name}</b> escaped the jungle!", 
            "🙊 <b>{user_name}</b> speaking Monkey! 🐒", "🦍 <b>{user_name}</b> is the King! 👑", 
            "🦍 <b>{user_name}</b> is going APE! 🔥", "🙉 Hears no evil, acts like it!", 
            "🍌 Keep <b>{user_name}</b> away from fruit!", "🐒 <b>{user_name}</b> climbing trees!"
        ],
        "couple": [
            "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({pct}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! ({pct}%) 🔔", 
            "🔥 <b>{u1}</b> ❤️ <b>{u2}</b> = Hottest Pair! ({pct}% fire)", "💔 {pct}% chemistry. Friends! 🫂", 
            "🏩 <b>{u1}</b> & <b>{u2}</b> need a room! ({pct}% spicy)", "✨ Destined: <b>{u1}</b> ❤️ <b>{u2}</b>! ({pct}%) 🌌", 
            "🧸 Cute match! ({pct}%) 🍭", "🥊 In a boxing ring! ({pct}%) 🥊", 
            "🍭 Sweet together! ({pct}%) 🍬", "🚢 Shipping <b>{u1}</b> & <b>{u2}</b>! ({pct}%) ⚓"
        ]
    }

    if cmd in mapping:
        msgs = mapping[cmd]
        users = list(daily_locks[chat_id]['seen_users'].values())
        if not users: return await update.message.reply_text("I need to see some people first! 🤡")
        
        if cmd == "couple":
            if len(users) < 2: res = "Need more people to find a couple! 💔"
            else:
                m = random.sample(users, 2)
                res = random.choice(msgs).format(u1=safe_h(m[0].first_name), u2=safe_h(m[1].first_name), pct=random.randint(1, 100))
        elif cmd == "chammar":
            res = random.choice(msgs).format(pct=random.randint(1, 100))
        else:
            m = random.choice(users)
            res = random.choice(msgs).format(user_name=safe_h(m.first_name), pct=random.randint(0, 100))
        
        daily_locks[chat_id]['commands'][cmd] = {'msg': res}
        await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

# --- Server & Main ---
@app.route('/')
def health(): return jsonify({"status": "active"})

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    port = int(os.environ.get('PORT', 10000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    
    bot = Application.builder().token(token).build()
    bot.add_error_handler(error_handler)
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_handler), group=-1)
    
    for c in ["chammar", "gay", "roast", "aura", "horny", "brain", "monkey", "couple"]:
        bot.add_handler(CommandHandler(c, fun_dispatcher))
    
    bot.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
