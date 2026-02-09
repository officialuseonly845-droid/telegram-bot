import os, logging, random, threading, html, httpx, asyncio, traceback
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask, jsonify
from aiohttp.web import Application as AioApp, AppRunner, TCPSite, Response
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Global State ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
daily_locks, chat_counters, manual_api_choice = {}, {}, {}
games = {} 
naughty_index = {} 
lock_mutex = threading.Lock()

# --- Config ---
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
WAKE_WORD = "beluga"

# Yahan apni File IDs daalte rehna
NAUGHTY_PHOTOS = ["https://docs.google.com/uc?export=download&id=1ha0a76nLE61Wkl-GTChueWzFzBzg9Evm"]

BELUGA_IDENTITY = "Your name is Beluga. Tum ek savage aur witty Telegram bot ho. Hinglish mein reply karo. Answer 1-2 sentences max."

if GROQ_KEY: groq_client = Groq(api_key=GROQ_KEY)

# --- Tic-Tac-Toe Helpers ---
def draw_tt_board(board):
    kb = []
    for i in range(0, 9, 3):
        row = [InlineKeyboardButton(board[i+j] if board[i+j] != "-" else " - ", callback_data=f"tt_{i+j}") for j in range(3)]
        kb.append(row)
    return InlineKeyboardMarkup(kb)

def check_winner(b):
    pts = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for p in pts:
        if b[p[0]] == b[p[1]] == b[p[2]] != "-": return b[p[0]]
    return "Draw" if "-" not in b else None

def init_chat_data(chat_id):
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    with lock_mutex:
        if chat_id not in daily_locks or daily_locks[chat_id].get('date') != today:
            daily_locks[chat_id] = {'date': today, 'commands': {}, 'seen_users': {}}
        if chat_id not in chat_counters: chat_counters[chat_id] = 0

# --- AI Engine ---
async def get_ai_response(user_text):
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post("https://openrouter.ai/api/v1/chat/completions", 
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                json={"model": "liquid/lfm-2.5-1.2b-thinking:free", "messages": [{"role": "system", "content": BELUGA_IDENTITY}, {"role": "user", "content": user_text}]})
            return r.json()['choices'][0]['message']['content']
    except: return "Dimaag mat kha, network down hai."

# --- Handlers ---
async def tictac_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, user, reply = update.effective_chat.id, update.effective_user, update.message.reply_to_message
    p1_id, p1_name = user.id, user.first_name
    p2_id, p2_name = (reply.from_user.id, reply.from_user.first_name) if reply else (context.bot.id, "Beluga")
    games[chat_id] = {'board': ["-"]*9, 'players': {p1_id: {"n": p1_name, "s": "❌"}, p2_id: {"n": p2_name, "s": "⭕"}}, 'turn': p1_id}
    await update.message.reply_text(f"<b>{p1_name} (❌) vs. {p2_name} (⭕)</b>\n\nKhel shuru!", reply_markup=draw_tt_board(games[chat_id]['board']), parse_mode=ParseMode.HTML)

async def naughty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    naughty_index[chat_id] = 0
    kb = [[InlineKeyboardButton("Next Photo ➡️ 🌸", callback_data="ng_next"), InlineKeyboardButton("Refresh 🔃 🍎", callback_data="ng_ref")]]
    await update.message.reply_photo(photo=NAUGHTY_PHOTOS[0], caption=f"🔞 **Collection**\nPhoto: 1 / {len(NAUGHTY_PHOTOS)}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def get_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        await update.message.reply_text(f"✅ **ID:** `{update.message.photo[-1].file_id}`", parse_mode=ParseMode.MARKDOWN)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data, chat_id, uid = query.data, query.message.chat.id, query.from_user.id
    if data.startswith("tt_"):
        if chat_id not in games or uid != games[chat_id]['turn']: return await query.answer("Wait kar!")
        idx = int(data.split("_")[1])
        if games[chat_id]['board'][idx] != "-": return await query.answer("Occupied!")
        games[chat_id]['board'][idx] = games[chat_id]['players'][uid]['s']
        win = check_winner(games[chat_id]['board'])
        if win:
            p = games[chat_id]['players']
            await query.edit_message_text(f"Congratulations {p[uid]['n']}! 🎉\n<b>Winner: {p[uid]['n']}</b>", reply_markup=draw_tt_board(games[chat_id]['board']), parse_mode=ParseMode.HTML)
            del games[chat_id]
        else:
            ids = list(games[chat_id]['players'].keys())
            games[chat_id]['turn'] = ids[1] if uid == ids[0] else ids[0]
            await query.edit_message_text(f"Turn: {games[chat_id]['players'][games[chat_id]['turn']]['n']}", reply_markup=draw_tt_board(games[chat_id]['board']))
    elif data.startswith("ng_"):
        idx = (naughty_index.get(chat_id, 0) + 1) % len(NAUGHTY_PHOTOS) if data == "ng_next" else random.randint(0, len(NAUGHTY_PHOTOS)-1)
        naughty_index[chat_id] = idx
        await query.edit_message_media(media=InputMediaPhoto(media=NAUGHTY_PHOTOS[idx], caption=f"🔞 Photo: {idx+1}/{len(NAUGHTY_PHOTOS)}"), reply_markup=query.message.reply_markup)
    await query.answer()

async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    
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
            "💎 <b>{user_name}</b> has {pct}% diamond aura! ✨", "🗿 <b>{user_name}</b> aura: {pct}% Chad! 🗿"
        ],
        "brain": [
            "🧠 <b>{user_name}</b>'s brain cells: {pct}% 🔋", "💡 <b>{user_name}</b>'s lightbulb: {pct}%! 🕯️",
            "🥔 <b>{user_name}</b>'s IQ: {pct}% (Potato) 🥔", "⚙️ Processing at {pct}%! ⚙️"
        ],
        "monkey": ["🐒 <b>{user_name}</b> is the group MONKEY! 🙈", "🍌 <b>{user_name}</b> Banana Lover! 🐵", "🐒 <b>{user_name}</b> is {pct}% chimpanzee!"],
        "couple": ["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({pct}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! ({pct}%) 🔔"]
    }

    if cmd in daily_locks[chat_id]['commands']: return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]['msg']}", parse_mode=ParseMode.HTML)
    users = list(daily_locks[chat_id]['seen_users'].values())
    if not users: return await update.message.reply_text("Bande kahan hain? 🤡")
    
    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(mapping[cmd]).format(u1=html.escape(m[0].first_name), u2=html.escape(m[1].first_name), pct=random.randint(1, 100))
    else:
        m = random.choice(users)
        res = random.choice(mapping[cmd]).format(user_name=html.escape(m.first_name), pct=random.randint(0, 100))
    
    daily_locks[chat_id]['commands'][cmd] = {'msg': res}
    await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

async def core_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id
    init_chat_data(chat_id)
    daily_locks[chat_id]['seen_users'][update.effective_user.id] = update.effective_user
    text = (update.message.text or "").lower()
    if WAKE_WORD in text or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
        await update.message.reply_text(await get_ai_response(text))

async def main():
    bot = Application.builder().token(os.environ.get('TELEGRAM_BOT_TOKEN')).build()
    bot.add_handler(CommandHandler("tictac", tictac_handler))
    bot.add_handler(CommandHandler("naughty", naughty_handler))
    bot.add_handler(CallbackQueryHandler(callback_handler))
    bot.add_handler(MessageHandler(filters.PHOTO & filters.FORWARDED, get_id_handler))
    for c in ["chammar", "gay", "roast", "aura", "brain", "monkey", "couple"]: bot.add_handler(CommandHandler(c, fun_dispatcher))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    await bot.initialize(); await bot.start(); await bot.updater.start_polling(drop_pending_updates=True)
    while True: await asyncio.sleep(3600)

if __name__ == '__main__': asyncio.run(main())
