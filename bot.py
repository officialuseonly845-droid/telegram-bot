import os, logging, random, threading, html, httpx, asyncio
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Global State ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
daily_locks, chat_counters = {}, {}
games, naughty_index = {}, {}
lock_mutex = threading.Lock()

# --- Config ---
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
WAKE_WORD = "beluga"
NAUGHTY_PHOTOS = ["https://docs.google.com/uc?export=download&id=1ha0a76nLE61Wkl-GTChueWzFzBzg9Evm"]
BELUGA_IDENTITY = "Your name is Beluga. Tum ek savage aur witty Telegram bot ho. Hinglish mein reply karo."

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
    except: return "Net slow hai, dimaag mat kha."

# --- Handlers ---
async def tictac_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, user, reply = update.effective_chat.id, update.effective_user, update.message.reply_to_message
    init_chat_data(chat_id)
    p1_id, p1_name = user.id, user.first_name
    p2_id, p2_name = (reply.from_user.id, reply.from_user.first_name) if reply else (context.bot.id, "Beluga")
    games[chat_id] = {'board': ["-"]*9, 'players': {p1_id: {"n": p1_name, "s": "❌"}, p2_id: {"n": p2_name, "s": "⭕"}}, 'turn': p1_id}
    await update.message.reply_text(f"<b>{p1_name} (❌) vs. {p2_name} (⭕)</b>\n\nKhel shuru!", reply_markup=draw_tt_board(games[chat_id]['board']), parse_mode=ParseMode.HTML)

async def naughty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    naughty_index[chat_id] = 0
    kb = [[InlineKeyboardButton("Next Photo ➡️ 🌸", callback_data="ng_next"), InlineKeyboardButton("Refresh 🔃 🍎", callback_data="ng_ref")]]
    await update.message.reply_photo(photo=NAUGHTY_PHOTOS[0], caption=f"🔞 Photo: 1 / {len(NAUGHTY_PHOTOS)}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data, chat_id, uid = query.data, query.message.chat.id, query.from_user.id
    if data.startswith("tt_"):
        if chat_id not in games or uid != games[chat_id]['turn']: return await query.answer("Wait kar!")
        idx = int(data.split("_")[1]); b = games[chat_id]['board']
        if b[idx] != "-": return await query.answer("Occupied!")
        b[idx] = games[chat_id]['players'][uid]['s']; win = check_winner(b)
        if win:
            p = games[chat_id]['players']
            await query.edit_message_text(f"Congratulations {p[uid]['n']}! 🎉\n\n{p[list(p.keys())[0]]['n']} vs {p[list(p.keys())[1]]['n']}\n<b>{p[uid]['n']} wins! Well played!</b> ❤️", reply_markup=draw_tt_board(b), parse_mode=ParseMode.HTML)
            del games[chat_id]
        else:
            ids = list(games[chat_id]['players'].keys())
            games[chat_id]['turn'] = ids[1] if uid == ids[0] else ids[0]
            await query.edit_message_text(f"Turn: {games[chat_id]['players'][games[chat_id]['turn']]['n']}", reply_markup=draw_tt_board(b))
    elif data.startswith("ng_"):
        idx = (naughty_index.get(chat_id, 0) + 1) % len(NAUGHTY_PHOTOS) if data == "ng_next" else random.randint(0, len(NAUGHTY_PHOTOS)-1)
        naughty_index[chat_id] = idx
        await query.edit_message_media(media=InputMediaPhoto(media=NAUGHTY_PHOTOS[idx], caption=f"🔞 Photo: {idx+1}/{len(NAUGHTY_PHOTOS)}"), reply_markup=query.message.reply_markup)
    await query.answer()

async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id = update.effective_chat.id; init_chat_data(chat_id)
    if cmd in daily_locks[chat_id]['commands']: return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]['msg']}", parse_mode=ParseMode.HTML)
    users = list(daily_locks[chat_id]['seen_users'].values())
    if not users: return await update.message.reply_text("Bande kam hain!")

    # --- ALL 78+ REPLIES INTEGRATED ---
    mapping = {
        "gay": [
            "🌈 <b>{user_name}</b> is {pct}% GAY! 🌚", "💅 <b>{user_name}</b> is a Diva! {pct}% ✨", "🍭 Rainbow-coded: <b>{user_name}</b> ({pct}%) 🏳️‍🌈", 
            "💄 Heterosexuality dropped: {pct}% 📉", "👠 <b>{user_name}</b>, slay queen! {pct}% 👑", "🏳️‍🌈 Proudly Gay: <b>{user_name}</b> ({pct}%)",
            "🌈 <b>{user_name}</b> is {pct}% into boys! 👦", "💅 Fabulous meter: {pct}% for <b>{user_name}</b>", "🦄 Unicorn energy: {pct}%! 🌈",
            "🍭 Sweet & Gay: <b>{user_name}</b> ({pct}%)", "✨ {user_name} is {pct}% glittery! 🏳️‍🌈"
        ],
        "roast": [
            "💀 <b>{user_name}</b> is pure garbage! 🚮", "🗑️ Face is a crime scene: <b>{user_name}</b>! 😭", "🦴 Starving for attention: <b>{user_name}</b>! 🦴", 
            "🤡 Dropped your brain? {user_name} 🚫", "🔥 Roasted like a chicken: <b>{user_name}</b>! 🍗", "🚑 Mental help needed for <b>{user_name}</b>! 💨",
            "📉 <b>{user_name}</b>'s IQ: Error 404! 🚫", "🧟 Zombies won't eat <b>{user_name}</b>... no brains! 🧠", "🚮 <b>{user_name}</b> is the reason why shampoo has instructions! 🧴",
            "💩 <b>{user_name}</b>'s birth certificate is an apology from the condom factory! 👶", "🛑 Stop talking, <b>{user_name}</b>, you're lowering the IQ of the whole chat! 📉"
        ],
        "chammar": [
            "🚽 <b>Shakti</b> (Harpic CEO) spotted! 🧴", "🧹 <b>Shakti</b>'s mop is smarter than them! 🏆", "🧴 Perfume? Harpic Blue for <b>Shakti</b>! 🧼", 
            "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽", "🧼 <b>Shakti</b> wash the floor! {pct}% done! 🧹", "🤡 Circus called, <b>Shakti</b>, they want their clown back! 🎪",
            "🪣 <b>Shakti</b>'s bucket list: Just a bucket! 🪣", "🧹 Clean it up, <b>Shakti</b>! Harpic is waiting! 🧴", "🧼 <b>Shakti</b>'s only talent: Scrubbing! 🧼",
            "🚽 <b>Shakti</b>'s castle is the public urinal! 🏰", "🧴 <b>Shakti</b> drinks Harpic for breakfast! 🥛"
        ],
        "aura": [
            "✨ <b>{user_name}</b>'s Aura: {pct}% 👑", "📉 -{pct} Aura for <b>{user_name}</b>! 💀", "🌟 Glowing at {pct}%! 🌌", "🌑 Cardboard Aura: {pct}% 📦",
            "🔥 Godly Aura: <b>{user_name}</b> ({pct}%)! ⚡", "💩 Shitty Aura: <b>{user_name}</b> ({pct}%)! 🤢", "🗿 Chad Aura: <b>{user_name}</b> ({pct}%)! 🗿",
            "🤡 Clown Aura: <b>{user_name}</b> ({pct}%)! 🎪", "🌈 Rainbow Aura: <b>{user_name}</b> ({pct}%)! 🏳️‍🌈", "💎 Diamond Aura: <b>{user_name}</b> ({pct}%)! ✨"
        ],
        "monkey": [
            "🐒 <b>{user_name}</b> is {pct}% Gorilla! 🦍", "🍌 Banana lover: <b>{user_name}</b>! 🐵", "🐒 Jungle king: <b>{user_name}</b>! ({pct}%) 🌲",
            "🦧 <b>{user_name}</b> is a pure Orangutan! 🐵", "🐒 Monkey business detected from <b>{user_name}</b>! 🍌"
        ],
        "couple": [
            "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({pct}% match!) 🏩", "💍 Wedding bells for <b>{u1}</b> & <b>{u2}</b>! {pct}% 🔔", "🔥 Toxic goals: <b>{u1}</b> & <b>{u2}</b>! {pct}% ☢️",
            "💕 Rab Ne Bana Di Jodi: <b>{u1}</b> & <b>{u2}</b>! ({pct}%) 🥰", "💔 Breakup loading for <b>{u1}</b> & <b>{u2}</b>! {pct}% 📉", "🥀 One-sided love: <b>{u1}</b> for <b>{u2}</b>! ({pct}%) 😭"
        ],
        "brain": [
            "🧠 <b>{user_name}</b>'s Brain: {pct}% 🔋", "💡 Intelligence: <b>{user_name}</b> ({pct}%)! 🕯️", "🥔 Potato Brain: <b>{user_name}</b> ({pct}%)! 🥔",
            "⚙️ Processing... <b>{user_name}</b> is {pct}% slow! 🐌", "🧠 Big Brain Energy: <b>{user_name}</b> ({pct}%)! ⚡"
        ]
    }

    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(mapping[cmd]).format(u1=html.escape(m[0].first_name), u2=html.escape(m[1].first_name), pct=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(mapping[cmd]).format(user_name=html.escape(m.first_name), pct=random.randint(0, 100))
    daily_locks[chat_id]['commands'][cmd] = {'msg': res}
    await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

async def core_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id = update.effective_chat.id; init_chat_data(chat_id)
    daily_locks[chat_id]['seen_users'][update.effective_user.id] = update.effective_user
    text = (update.message.text or "").lower()
    if WAKE_WORD in text or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
        await update.message.reply_text(await get_ai_response(text))

async def main():
    bot = Application.builder().token(os.environ.get('TELEGRAM_BOT_TOKEN')).build()
    bot.add_handler(CommandHandler("tictac", tictac_handler))
    bot.add_handler(CommandHandler("naughty", naughty_handler))
    bot.add_handler(CallbackQueryHandler(callback_handler))
    bot.add_handler(MessageHandler(filters.PHOTO & filters.FORWARDED, lambda u, c: u.message.reply_text(f"✅ ID: `{u.message.photo[-1].file_id}`", parse_mode=ParseMode.MARKDOWN)))
    for c in ["chammar", "gay", "roast", "aura", "couple", "monkey", "brain"]: bot.add_handler(CommandHandler(c, fun_dispatcher))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    await bot.initialize(); await bot.start(); await bot.updater.start_polling(drop_pending_updates=True)
    while True: await asyncio.sleep(3600)

if __name__ == '__main__': asyncio.run(main())
