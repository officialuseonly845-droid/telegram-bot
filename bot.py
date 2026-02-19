import os, logging, random, html, json, asyncio, requests
from datetime import datetime
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Flask ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
server = Flask('')
@server.route('/')
def home(): return "Miko is Awake! ✨"
def run_web(): server.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

# --- Config ---
TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]

# --- Persistence ---
DATA_FILE = "miko_data.json"
def load_data():
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {"seen": {}, "daily": {}, "config": {"model": "auto"}}

data_store = load_data()
seen_users, daily_locks, config = data_store["seen"], data_store["daily"], data_store["config"]
games = {}

def save_data():
    with open(DATA_FILE, 'w') as f: json.dump(data_store, f)

# --- Funny & Naughty Replies Database ---
MAPPING = {
    "gay": [
        "🌈 <b>{u}</b> is {p}% GAY! Blush mat karo, sab dikh raha hai ✨🌚",
        "💅 Diva meter: {p}% for <b>{u}</b>! Slayy Queen! ✨",
        "💄 Gay radar: {p}% on <b>{u}</b>! Lipstick kahan chhupayi hai? 🏳️‍🌈",
        "👦 <b>{u}</b> loves boys {p}%! Rainbow boy spotted! 💖",
        "🍭 Sweet & Gay: <b>{u}</b> ({p}%)! Candy boy vibes! 🌈",
        "👠 <b>{u}</b>, heels pehen ke thoda matak ke chalo! {p}% Chhamiya! 💅",
        "🎀 <b>{u}</b> is {p}% feminine tonight! Ribbon bandh lo! ✨",
        "🏳️‍🌈 Radar says <b>{u}</b> is {p}% Rainbow lover! Pride parade chalo! 🏳️‍🌈"
    ],
    "couple": [
        "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩",
        "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! {p}% Pyar, 100% Kalesh! 🔔",
        "🔥 Toxic Goals: <b>{u1}</b> & <b>{u2}</b>! Ek-dusre ka sir phodne wale hain! ☢️",
        "💕 Rab Ne Bana Di Jodi: <b>{u1}</b> aur <b>{u2}</b>! 😂 🥰",
        "💔 Breakup Loading for <b>{u1}</b> & <b>{u2}</b>! Block list taiyaar hai! 📉",
        "🔞 Oye-Hoye! <b>{u1}</b> aur <b>{u2}</b>! {p}% chances hain ki aaj raat 'Kaand' hoga! 🌚",
        "💰 <b>{u1}</b> ne <b>{u2}</b> ko sirf paison ke liye fasaya hai! {p}% Sach! 💸",
        "🎭 Acting band karo <b>{u1}</b> & <b>{u2}</b>! Sabko pata hai tum single ho! 🤡"
    ],
    "aura": [
        "✨ <b>{u}</b>'s Aura: {p}% 👑 (Royal Vibes!)",
        "🗿 Chad Aura: <b>{u}</b> ({p}%)! Sigma spotted! 🗿",
        "💎 Diamond Aura: <b>{u}</b> ({p}%)! Chamak rahe ho! ✨",
        "💀 Negative Aura: <b>{u}</b> ({p}%)! Thoda naha liya karo! 🧼",
        "🔥 Gangster Aura: <b>{u}</b> ({p}%)! Area ka bhai! 🔫",
        "🌈 Cute Aura: <b>{u}</b> ({p}%)! Pura baby boy lag raha hai! 🧸",
        "📉 Noob Aura: <b>{u}</b> ({p}%)! Kya gunda banega re tu! 🤡",
        "🌟 God Level Aura: <b>{u}</b> ({p}%)! Sab jhuk ke pranam karo! 🙏"
    ]
}

# --- Tic-Tac-Toe Logic ---
def draw_tt(board):
    kb = []
    for i in range(0, 9, 3):
        row = [InlineKeyboardButton("⬜" if board[i+j]=="-" else ("🟥" if board[i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)]
        kb.append(row)
    return InlineKeyboardMarkup(kb)

async def tictac_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = str(update.effective_user.id), str(update.effective_chat.id)
    reply = update.message.reply_to_message
    p1_n = update.effective_user.first_name
    p2_id, p2_n = (str(reply.from_user.id), reply.from_user.first_name) if reply and not reply.from_user.is_bot else (str(context.bot.id), "Miko 🤖")
    games[cid] = {'b': ["-"]*9, 'p': {uid: "X", p2_id: "O"}, 'turn': uid, 'names': {uid: p1_n, p2_id: p2_n}, 'allowed': [uid, p2_id]}
    await update.message.reply_text(f"🎮 <b>{p1_n} vs {p2_n}</b>\nBaari: {p1_n}", reply_markup=draw_tt(games[cid]['b']), parse_mode=ParseMode.HTML)

# --- Universal Callback ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d = q.data; uid = str(q.from_user.id); cid = str(q.message.chat.id)
    
    if d.startswith("tt_"):
        # 🔒 3rd Person Security (Show Alert only to them)
        if cid not in games or uid not in games[cid]['allowed']:
            return await q.answer("Ara bhai ye tera match nhi ha... Apna game shuru kar! 💋", show_alert=True)
        
        g = games[cid]; idx = int(d.split("_")[1])
        if uid != g['turn'] or g['b'][idx] != "-": return await q.answer("Ruk ja bhai, abhi teri baari nahi hai! ✨")
        
        await q.answer(); g['b'][idx] = g['p'][uid]
        nxt = [p for p in g['allowed'] if p != uid][0]; g['turn'] = nxt
        await q.edit_message_text(f"👉 Baari: <b>{g['names'][nxt]}</b>", reply_markup=draw_tt(g['b']), parse_mode=ParseMode.HTML)

    if d == "kt_next":
        await q.answer("Next Kitty! 💋"); url = requests.get("https://api.thecatapi.com/v1/images/search").json()[0]['url']
        await q.edit_message_media(media=requests.get(url).content) # Simplified for example

# --- Fun Dispatcher (/gay, /couple, /aura) ---
async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    cid = str(update.effective_chat.id)
    users = list(seen_users.get(cid, {}).values())
    if not users: return await update.message.reply_text("Group mein bakchodi karo pehle! 🤡")

    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(MAPPING[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(MAPPING[cmd]).format(u=m['n'], p=random.randint(0, 100))
    await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

async def tracker(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.effective_user or u.effective_user.is_bot: return
    cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
    if cid not in seen_users: seen_users[cid] = {}
    seen_users[cid][uid] = {"n": u.effective_user.first_name}; save_data()

async def main():
    Thread(target=run_web).start()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("kitty", lambda u,c: u.message.reply_photo("https://api.thecatapi.com/v1/images/search", caption="🐱 Meow!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ɴᴇxᴛ 💋", callback_data="kt_next"), InlineKeyboardButton("ʀᴇꜰʀᴇsʜ 💀", callback_data="kt_refresh")]]))))
    for c in MAPPING.keys(): app.add_handler(CommandHandler(c, fun_dispatcher))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL, tracker), group=1)
    
    print("✅ Miko Final Version Live! 🚀")
    await app.initialize(); await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__': asyncio.run(main())
