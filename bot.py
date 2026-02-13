import os, logging, random, html, json, asyncio, requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Config ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]
KITTY_PHOTOS = ["https://docs.google.com/uc?export=download&id=1ha0a76nLE61Wkl-GTChueWzFzBzg9Evm"]

# --- Persistence ---
DATA_FILE = "miko_master_data.json"
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: return {"daily": {}, "seen": {}, "config": {"model": "auto"}}
    return {"daily": {}, "seen": {}, "config": {"model": "auto"}}

data_store = load_data()
daily_locks, seen_users, config = data_store["daily"], data_store["seen"], data_store["config"]
games, kitty_index = {}, {}

def save_data():
    with open(DATA_FILE, 'w') as f: json.dump(data_store, f)

# --- AI Logic ---
async def get_miko_reply(text):
    mode = config.get("model", "auto")
    sys_p = "Name: Miko. Female, 20-24. Persona: Cute, witty, teasing, Hinglish. Short sentences. Use ✨😊💫."
    def call_api(url, key, model):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": text}]}, timeout=10)
            return r.json()['choices'][0]['message']['content']
        except: return None
    return call_api("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "liquid/lfm-40b") or "Miko busy hai! ✨"

# --- 78+ Replies ---
MAPPING = {
    "gay": ["🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 <b>{u}</b> Diva meter: {p}%! ✨", "💄 Gay radar: {p}% for <b>{u}</b>! 🏳️‍🌈", "👦 <b>{u}</b> loves boys {p}%! 💖"],
    "roast": ["💀 <b>{u}</b> is pure garbage! 🚮", "🗑️ <b>{u}</b>'s face is a crime scene! 😭", "🤡 <b>{u}</b> dropped brain cell! 🚫"],
    "chammar": ["🚽 <b>Shakti</b> (Harpic CEO) spotted! 🧴", "🧹 <b>Shakti</b>'s mop is smarter! 🏆", "🪠 Sultan of Sewage: <b>Shakti</b>!"],
    "aura": ["✨ <b>{u}</b>'s Aura: {p}% 👑", "🗿 Chad Aura: <b>{u}</b> ({p}%)! 🗿", "💎 Diamond Aura: <b>{u}</b> ({p}%)! ✨"],
    "monkey": ["🐒 <b>{u}</b> is {p}% Gorilla! 🦍", "🍌 Banana lover: <b>{u}</b>! 🐵"],
    "couple": ["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! {p}% 🔔"],
    "brain": ["🧠 <b>{u}</b>'s Brain: {p}% 🔋", "🥔 Potato Brain: <b>{u}</b> ({p}%)! 🥔"]
}

# --- UI & Helpers ---
def draw_tt_board(board):
    kb = []
    for i in range(0, 9, 3):
        row = [InlineKeyboardButton("⬜" if board[i+j]=="-" else ("🟥" if board[i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)]
        kb.append(row)
    return InlineKeyboardMarkup(kb)

# --- Handlers ---
async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    cid, today = str(update.effective_chat.id), str(datetime.now().date())
    if cid not in daily_locks or daily_locks[cid].get("date") != today: daily_locks[cid] = {"date": today, "cmds": {}}
    if cmd in daily_locks[cid]["cmds"]: return await update.message.reply_text(f"📌 {daily_locks[cid]['cmds'][cmd]}", parse_mode=ParseMode.HTML)
    users = list(seen_users.get(cid, {}).values())
    if not users: return await update.message.reply_text("Group mein bakchodi karo pehle! 🤡")
    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(MAPPING[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(MAPPING[cmd]).format(u=m['n'], p=random.randint(0, 100))
    daily_locks[cid]["cmds"][cmd] = res; save_data()
    await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

async def miko_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("This command is meant for owner only 💋")
    kb = [[InlineKeyboardButton("OpenRouter 💎", callback_data="cfg_opr"), InlineKeyboardButton("Groq ⚡", callback_data="cfg_gro")]]
    await update.message.reply_text("🛠 <b>Miko Admin Panel</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def tictac_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = str(update.effective_user.id), str(update.effective_chat.id)
    reply = update.message.reply_to_message
    p1_n, p2_n = update.effective_user.first_name, (reply.from_user.first_name if reply and not reply.from_user.is_bot else "Miko 🤖")
    p2_id = str(reply.from_user.id) if reply and not reply.from_user.is_bot else str(context.bot.id)
    games[cid] = {'board': ["-"]*9, 'players': {uid: {"n": p1_n, "s": "X"}, p2_id: {"n": p2_n, "s": "O"}}, 'turn': uid, 'allowed': [uid, p2_id]}
    await update.message.reply_text(f"🎮 {p1_n} vs {p2_n}\nTurn: {p1_n}", reply_markup=draw_tt_board(games[cid]['board']))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d, uid, cid = q.data, str(q.from_user.id), str(q.message.chat.id)
    if d.startswith("cfg_"):
        if int(uid) not in ADMIN_IDS: return await q.answer("This command is meant for owner only 💋", show_alert=True)
        config["model"] = d.split("_")[1]; save_data(); await q.edit_message_text(f"✅ Model: {config['model']}")
    elif d.startswith("tt_"):
        if cid not in games or uid not in games[cid]['allowed']: return await q.answer("Apna game khelo! 🤡")
        g = games[cid]; b = g['board']; idx = int(d.split("_")[1])
        if uid != g['turn'] or b[idx] != "-": return await q.answer("Wait karo!")
        await q.answer(); b[idx] = g['players'][uid]['s']
        pts = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
        win = next((b[p[0]] for p in pts if b[p[0]]==b[p[1]]==b[p[2]] != "-"), None)
        if win or "-" not in b:
            txt = f"🏁 {g['players'][uid]['n']} Won!" if win else "🏁 Draw!"
            await q.edit_message_text(txt, reply_markup=draw_tt_board(b)); del games[cid]
        else:
            nxt = [i for i in g['allowed'] if i != uid][0]; g['turn'] = nxt
            await q.edit_message_text(f"👉 Turn: {g['players'][nxt]['n']}", reply_markup=draw_tt_board(b))

async def tracker(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.effective_user or u.effective_user.is_bot: return
    cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
    if cid not in seen_users: seen_users[cid] = {}
    seen_users[cid][uid] = {"n": u.effective_user.first_name}; save_data()

async def auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if f"@{context.bot.username.lower()}" in update.message.text.lower() or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
        res = await get_miko_reply(update.message.text); await update.message.reply_text(res)

async def main_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("miko", miko_admin_handler))
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("kitty", lambda u, c: u.message.reply_photo(KITTY_PHOTOS[0], "🐱", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Next", callback_data="kt_next")]]))))
    for c in MAPPING.keys(): app.add_handler(CommandHandler(c, fun_dispatcher))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply))
    app.add_handler(MessageHandler(filters.ALL, tracker), group=1)
    await app.initialize(); await app.start_polling(); await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main_bot())
