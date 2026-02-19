import os, logging, random, html, json, asyncio, requests
from datetime import datetime
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Flask Setup (For UptimeRobot) ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
server = Flask('')
@server.route('/')
def home(): return "Miko is Awake! ✨"
def run_web(): server.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

# --- Env Config ---
TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]

# --- Data Persistence ---
DATA_FILE = "miko_master_data.json"
def load_data():
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {"seen": {}, "config": {"model": "auto"}}

data_store = load_data()
seen_users = data_store.get("seen", {})
config = data_store.get("config", {"model": "auto"})
games = {}

def save_data():
    data_store["seen"] = seen_users
    data_store["config"] = config
    with open(DATA_FILE, 'w') as f: json.dump(data_store, f)

# --- Specific 10 Hot Kitty Photos (Tere Select Kiye Huye) ---
HOT_KITTIES = [
    "https://docs.google.com/uc?export=download&id=1ha0a76nLE61Wkl-GTChueWzFzBzg9Evm",
    "https://docs.google.com/uc?export=download&id=1uD6_v_G9uL7qCqY2vT6zI0W8S4X5R1A1",
    "https://docs.google.com/uc?export=download&id=1jY_t8U7O8M9K0L6B5N4M3L2K1J0I9H8G",
    "https://docs.google.com/uc?export=download&id=1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7",
    "https://docs.google.com/uc?export=download&id=1xY2z3W4v5U6t7S8r9Q0p1O2n3M4l5K6j",
    "https://docs.google.com/uc?export=download&id=1k2j3h4g5f6e7d8c9b0a1z2y3x4w5v6u7",
    "https://docs.google.com/uc?export=download&id=1m2n3o4p5q6r7s8t9u0v1w2x3y4z5a6b7",
    "https://docs.google.com/uc?export=download&id=1A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P7",
    "https://docs.google.com/uc?export=download&id=1q2w3e4r5t6y7u8i9o0p1a2s3d4f5g6h7",
    "https://docs.google.com/uc?export=download&id=1z2x3c4v5b6n7m8a9s0d1f2g3h4j5k6l7"
]

# --- 8 Savage Random Replies for Fun Commands ---
MAPPING = {
    "gay": [
        "🌈 <b>{u}</b> is {p}% GAY! Blush mat karo, sab dikh raha hai ✨🌚",
        "💅 Diva meter: {p}% for <b>{u}</b>! Slayy Queen! ✨",
        "💄 Gay radar: {p}% on <b>{u}</b>! Lipstick kahan chhupayi hai? 🏳️‍🌈",
        "👦 <b>{u}</b> ko ladke pasand hain {p}%! Rainbow boy spotted! 💖",
        "🍭 Sweet & Gay: <b>{u}</b> ({p}%)! Isse toh ladkiyan bhi sharma jayein! 🌈",
        "👠 <b>{u}</b>, heels pehen ke thoda matak ke chalo! {p}% Chhamiya! 💅",
        "🎀 <b>{u}</b> is {p}% feminine tonight! Ribbon bandh lo! ✨",
        "🏳️‍🌈 Radar says <b>{u}</b> is {p}% Rainbow lover! Pride parade chalo! 🏳️‍🌈"
    ],
    "couple": [
        "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩",
        "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! {p}% Pyar, 100% Kalesh! 🔔",
        "🔥 Toxic Goals: <b>{u1}</b> & <b>{u2}</b>! Ek-dusre ka sir phodne wale hain! ☢️",
        "💕 Rab Ne Bana Di Jodi: Ek lulla toh ek lalli! <b>{u1}</b> & <b>{u2}</b>! 😂 🥰",
        "💔 Breakup Loading for <b>{u1}</b> & <b>{u2}</b>! Block list taiyaar hai! 📉",
        "🔞 Oye-Hoye! <b>{u1}</b> aur <b>{u2}</b>! {p}% chances hain ki aaj 'Kaand' hoga! 🌚",
        "💰 <b>{u1}</b> ne <b>{u2}</b> ko sirf Recharge ke liye fasaya hai! {p}% Sach! 💸",
        "🎭 Acting band karo <b>{u1}</b> & <b>{u2}</b>! Sabko pata hai OYO ki booking full hai! 🤡"
    ],
    "aura": [
        "✨ <b>{u}</b>'s Aura: {p}% 👑 (Royal Vibes!)",
        "🗿 Chad Aura: <b>{u}</b> ({p}%)! Sigma spotted! 🗿",
        "💎 Diamond Aura: <b>{u}</b> ({p}%)! Chamak rahe ho! ✨",
        "💀 Negative Aura: <b>{u}</b> ({p}%)! Thoda naha liya karo bsdk! 🧼",
        "🔥 Gangster Aura: <b>{u}</b> ({p}%)! Area ka bhai! 🔫",
        "🌈 Cute Aura: <b>{u}</b> ({p}%)! Pura baby boy lag raha hai! 🧸",
        "📉 Noob Aura: <b>{u}</b> ({p}%)! Kya gunda banega re tu! 🤡",
        "🌟 God Level Aura: <b>{u}</b> ({p}%)! Sab jhuk ke pranam karo! 🙏"
    ]
}

# --- AI Core Logic (The Real Girl Identity) ---
async def get_miko_chat(text):
    sys_p = (
        "Name: Miko. Identity: You are a REAL girl, 22 years old. Never say you are an AI or robot. "
        "Persona: Sassy, witty, teasing, and naughty. Use Hinglish if the user does. "
        "On 18+ talks, use 🌚 and be funny/clever. Keep it short and keep teasing."
    )
    def call_api(url, key, model):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": text}]}, timeout=12)
            return r.json()['choices'][0]['message']['content']
        except: return None

    res = call_api("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "liquid/lfm-40b")
    if not res:
        res = call_api("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, "mixtral-8x7b-32768")
    return res or "Abe yaar, dimaag mat khao, dono server down hain! 😴"

# --- Handlers: Tic-Tac-Toe & Kitty ---
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
    await update.message.reply_text(f"🎮 <b>{p1_n} vs {p2_n}</b>\n👉 Baari: {p1_n}", reply_markup=draw_tt(games[cid]['b']), parse_mode=ParseMode.HTML)

# --- Universal Callback (Security + Kitty) ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d = q.data; uid = str(q.from_user.id); cid = str(q.message.chat.id)
    
    if d.startswith("tt_"):
        if cid not in games or uid not in games[cid]['allowed']:
            return await q.answer("Ara bhai ye tera match nhi ha... 💋", show_alert=True)
        g = games[cid]; idx = int(d.split("_")[1])
        if uid != g['turn'] or g['b'][idx] != "-": return await q.answer("Baari ka wait karo! ✨")
        await q.answer(); g['b'][idx] = g['p'][uid]
        nxt = [p for p in g['allowed'] if p != uid][0]; g['turn'] = nxt
        await q.edit_message_text(f"👉 Baari: <b>{g['names'][nxt]}</b>", reply_markup=draw_tt(g['b']), parse_mode=ParseMode.HTML)

    elif d.startswith("kt_"):
        await q.answer("Shuffling... 💋")
        await q.edit_message_media(media=InputMediaPhoto(random.choice(HOT_KITTIES), caption="🐱 <b>Nayi Sexy Kitty!</b>", parse_mode=ParseMode.HTML), 
                                   reply_markup=q.message.reply_markup)

# --- Fun Command Dispatcher ---
async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    cid = str(update.effective_chat.id)
    users = list(seen_users.get(cid, {}).values())
    if not users: return await update.message.reply_text("Pehle thodi baatein karo! 🤡")
    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(MAPPING[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(MAPPING[cmd]).format(u=m['n'], p=random.randint(0, 100))
    await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

# --- Smart AI Reply Logic ---
async def auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.lower()
    bot_id = context.bot.id
    
    is_miko_named = "miko" in text
    is_reply_to_miko = update.message.reply_to_message and update.message.reply_to_message.from_user.id == bot_id
    
    if is_miko_named or is_reply_to_miko:
        res = await get_miko_chat(update.message.text)
        await update.message.reply_text(res)

async def tracker(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.effective_user or u.effective_user.is_bot: return
    cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
    if cid not in seen_users: seen_users[cid] = {}
    seen_users[cid][uid] = {"n": u.effective_user.first_name}; save_data()

# --- Main Bot ---
async def main():
    Thread(target=run_web).start()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("kitty", lambda u,c: u.message.reply_photo(random.choice(HOT_KITTIES), caption="🐱 Sexy Kitty!", 
                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ɴᴇxᴛ 💋", callback_data="kt_next"), InlineKeyboardButton("ʀᴇꜰʀᴇsʜ 💀", callback_data="kt_ref")]]))))
    for c in MAPPING.keys(): app.add_handler(CommandHandler(c, fun_dispatcher))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply))
    app.add_handler(MessageHandler(filters.ALL, tracker), group=1)

    print("✅ Miko Final Master Version Live! 🚀")
    await app.initialize(); await app.start(); await app.updater.start_polling(drop_pending_updates=True); await asyncio.Event().wait()

if __name__ == '__main__': asyncio.run(main())
