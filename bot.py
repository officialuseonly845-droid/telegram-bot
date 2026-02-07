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

# Render Keep-Alive Script (aiohttp)
from aiohttp.web import Application as AioApp, AppRunner, TCPSite, Response

from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Global State ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
daily_locks = {}
chat_counters = {} 
manual_api_choice = {} 
lock_mutex = threading.Lock()

# --- Config ---
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]
WAKE_WORD = "beluga"

BELUGA_IDENTITY = (
    "Your name is Beluga. Tum ek savage aur witty Telegram bot ho. "
    "Hamesha Hinglish (Hindi + English) mein reply karo. "
    "Be sharp, sarcastic, and funny. Answer 1-2 sentences max."
)

if GROQ_KEY: groq_client = Groq(api_key=GROQ_KEY)

# --- Render Keep-Alive Server ---
async def checkHealth(request):
    return Response(text="Beluga is awake and savage!", content_type="text/plain")

async def startKeepAliveServer() -> None:
    port = int(os.environ.get('PORT', 8080))
    server_app = AioApp()
    server_app.router.add_get('/', checkHealth)
    server_app.router.add_get('/healthz', checkHealth)
    runner = AppRunner(server_app, access_log=None)
    await runner.setup()
    site = TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- Helpers ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"🚨 CRASH: {context.error}")

def safe_h(text): return html.escape(text or "Dost")

def init_chat_data(chat_id):
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    with lock_mutex:
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {'date': today, 'commands': {}, 'seen_users': {}}
        if chat_id not in chat_counters: chat_counters[chat_id] = 0

# --- AI Engine (Dedicated Callers) ---
async def try_openrouter(text):
    if not OPENROUTER_KEY: return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post("https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                json={"model": "liquid/lfm-2.5-1.2b-thinking:free", "messages": [{"role": "system", "content": BELUGA_IDENTITY}, {"role": "user", "content": text}]})
            return res.json()['choices'][0]['message']['content'] if res.status_code == 200 else None
    except: return None

async def try_nvidia(text):
    if not NVIDIA_KEY: return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post("https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {NVIDIA_KEY}"},
                json={"model": "nvidia/nemoretriever-page-elements-v3", "messages": [{"role": "user", "content": f"{BELUGA_IDENTITY}\n\nTask: {text}"}]})
            return res.json()['choices'][0]['message']['content'] if res.status_code == 200 else None
    except: return None

async def try_groq(text):
    if not GROQ_KEY: return None
    try:
        res = groq_client.chat.completions.create(model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": f"RULES: {BELUGA_IDENTITY}\n\nUSER: {text}"}])
        return res.choices[0].message.content
    except: return None

async def get_ai_response(chat_id, user_text, is_image=False):
    choice = manual_api_choice.get(chat_id)
    if choice == "opr": return await try_openrouter(user_text) or "OpenRouter nakhre kar raha hai."
    if choice == "nvi": return await try_nvidia(user_text) or "Nvidia offline hai."
    if choice == "gro": return await try_groq(user_text) or "Groq down hai."

    if is_image: return await try_nvidia(user_text) or await try_groq(user_text) or "Photo samajh nahi aayi."
    return await try_openrouter(user_text) or await try_nvidia(user_text) or await try_groq(user_text) or "Brains offline hain."

# --- Admin Feature: /belu ---
async def belu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return await update.message.reply_text("Beta admin bano pehle. 🤡")
    keyboard = [[InlineKeyboardButton("nvi", callback_data="nvi"), InlineKeyboardButton("gro", callback_data="gro")],
                [InlineKeyboardButton("opr", callback_data="opr"), InlineKeyboardButton("auto switch", callback_data="auto")]]
    await update.message.reply_text("current active brains 🧠", reply_markup=InlineKeyboardMarkup(keyboard))

async def api_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    choice = query.data
    manual_api_choice[update.effective_chat.id] = None if choice == "auto" else choice
    await query.edit_message_text(f"✅ **System Update:** Locked to {choice.upper() if choice != 'auto' else 'Auto Switch'}")

# --- Message Handler ---
async def core_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    
    # Store user (Privacy off ensures we see more people)
    daily_locks[chat_id]['seen_users'][update.effective_user.id] = update.effective_user
    
    # Reaction Logic
    with lock_mutex:
        chat_counters[chat_id] += 1
        if chat_counters[chat_id] >= 6:
            try: await update.message.set_reaction(reaction=random.choice(["🔥", "😂", "❤️", "⚡", "😈"])); chat_counters[chat_id] = 0
            except: pass

    text = (update.message.text or update.message.caption or "").lower()
    is_image = bool(update.message.photo)
    is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
    
    if WAKE_WORD in text or is_reply:
        await context.bot.send_chat_action(chat_id, "typing")
        await update.message.reply_text(await get_ai_response(chat_id, text, is_image))

# --- Fun Commands ---
async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    
    if cmd in daily_locks[chat_id]['commands']:
        return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]['msg']}", parse_mode=ParseMode.HTML)

    mapping = {
        "chammar": ["🚽 <b>Shakti</b> detected! Harpic CEO is here! 🧴🤡", "🧹 <b>Shakti</b>'s mop! 🏆", "🧴 <b>Shakti</b>'s perfume? Harpic Blue! 🧼", "🤡 <b>Shakti</b>'s dreams are flushed! 🌊", "🧼 <b>Shakti</b> drinks Harpic to stay clean! 💦", "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽", "🚽 <b>Shakti</b> is {pct}% Harpic! 💀"],
        "gay": ["🌈 Today's gay: <b>{user_name}</b> ({pct}%) 🌚", "🦄 <b>{user_name}</b> is fabulous! {pct}% 🏳️‍🌈💅", "💅 Slay <b>{user_name}</b>! {pct}% icon! ✨", "🎨 <b>{user_name}</b> is the rainbow! {pct}%"],
        "roast": ["💀 <b>{user_name}</b> is pure trash! 🚮", "🗑️ Mirror asked <b>{user_name}</b> for therapy! 😭", "🤡 <b>{user_name}</b> dropped their brain! 🚫", "🧟 Zombies won't eat <b>{user_name}</b>... no brains! 🧠"],
        "aura": ["✨ <b>{user_name}</b>'s aura: {pct}% 👑", "🗿 <b>{user_name}</b> aura: {pct}% Chad! 🗿", "💎 <b>{user_name}</b> has {pct}% diamond aura! ✨"],
        "horny": ["🚨 <b>{user_name}</b> horny level: {pct}% (BONK!) 🚔", "🥵 <b>{user_name}</b> is thirsty! {pct}% 💧", "❄️ <b>{user_name}</b> needs cold shower! {pct}%"],
        "brain": ["🧠 <b>{user_name}</b>'s brain cells: {pct}% 🔋", "🥔 <b>{user_name}</b>'s IQ: {pct}% (Potato) 🥔", "🤯 Using {pct}% of power! 🤯"],
        "monkey": ["🐒 <b>{user_name}</b> is the group MONKEY! 🙈", "🍌 <b>{user_name}</b> Banana Lover! 🐵", "🦍 <b>{user_name}</b> is going APE! 🔥"],
        "couple": ["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({pct}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! ({pct}%) 🔔", "🏩 <b>{u1}</b> & <b>{u2}</b> need a room! ({pct}% spicy)", "🚢 Shipping <b>{u1}</b> & <b>{u2}</b>! ({pct}%) ⚓"]
    }

    if cmd in mapping:
        users = list(daily_locks[chat_id]['seen_users'].values())
        if not users: return await update.message.reply_text("I need to see some people first! 🤡")
        
        if cmd == "couple":
            if len(users) < 2: res = "Need more people! 💔"
            else:
                m = random.sample(users, 2)
                res = random.choice(mapping[cmd]).format(u1=safe_h(m[0].first_name), u2=safe_h(m[1].first_name), pct=random.randint(1, 100))
        elif cmd == "chammar":
            res = random.choice(mapping[cmd]).format(pct=random.randint(1, 100))
        else:
            m = random.choice(users)
            res = random.choice(mapping[cmd]).format(user_name=safe_h(m.first_name), pct=random.randint(0, 100))
        
        daily_locks[chat_id]['commands'][cmd] = {'msg': res}
        await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

# --- Server & Main ---
@app.route('/')
def health(): return jsonify({"status": "active"})

async def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    await startKeepAliveServer()
    Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot = Application.builder().token(token).build()
    bot.add_handler(CommandHandler("belu", belu_command))
    bot.add_handler(CallbackQueryHandler(api_callback))
    bot.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, core_handler), group=-1)
    for c in ["chammar", "gay", "roast", "aura", "horny", "brain", "monkey", "couple"]:
        bot.add_handler(CommandHandler(c, fun_dispatcher))
    await bot.initialize(); await bot.start(); await bot.updater.start_polling(drop_pending_updates=True)
    while True: await asyncio.sleep(3600)

if __name__ == '__main__': asyncio.run(main())
