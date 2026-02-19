import os, logging, random, json, asyncio, requests
from datetime import datetime
from aiohttp.web import Application, AppRunner, TCPSite, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Render Server (For UptimeRobot) ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("MikoBot")

PORT = int(os.environ.get("PORT", 8080))
async def checkHealth(r): return Response(text="Miko is Ruling! 💋", content_type="text/plain")

async def startServer() -> None:
    app = Application()
    app.router.add_get('/', checkHealth)
    runner = AppRunner(app, access_log=None)
    await runner.setup()
    await TCPSite(runner, '0.0.0.0', PORT).start()

# --- Config & Data ---
TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

DATA_FILE = "miko_master.json"
def load_data():
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {"seen": {}, "locks": {}, "counts": {}}

data_store = load_data()
seen_users, locks, counts = data_store["seen"], data_store["locks"], data_store["counts"]
games = {}

def save_data():
    with open(DATA_FILE, 'w') as f: json.dump(data_store, f)

# --- 🔥 REPLIES ---
MAPPING = {
    "gay": ["🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 Diva meter: {p}% for <b>{u}</b>! ✨", "💄 Gay radar: {p}% on <b>{u}</b>! 🏳️‍🌈", "👠 <b>{u}</b>, heels peheno! {p}% Chhamiya! 💅", "🎀 <b>{u}</b> is {p}% feminine! ✨", "🍭 Sweet & Gay: <b>{u}</b> ({p}%)! 🦄", "🫦 <b>{u}</b> is {p}% bottom! 🍑"],
    "couple": ["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding: <b>{u1}</b> & <b>{u2}</b>! {p}% Pyar! 🔔", "🔥 Toxic: <b>{u1}</b> & <b>{u2}</b>! ☢️", "💕 Jodi: <b>{u1}</b> & <b>{u2}</b>! 🥰", "🔞 Oye-Hoye! <b>{u1}</b> aur <b>{u2}</b>! Kaand {p}%! 🌚", "🎭 Acting: <b>{u1}</b> & <b>{u2}</b>! OYO full hai! 🤡"],
    "aura": ["✨ <b>{u}</b> Aura: {p}% 👑", "🗿 Chad: <b>{u}</b> ({p}%)! 🗿", "💎 Diamond: <b>{u}</b> ({p}%)! ✨", "🔥 Gangster: <b>{u}</b> ({p}%)! 🔫", "🌟 God: <b>{u}</b> ({p}%)! 🙏", "🦁 Sher: <b>{u}</b> ({p}%)! 👑"]
}

# --- AI Chat ---
async def get_miko_chat(text):
    sys_p = "Your name is Miko, 22yo girl. Helpful, friendly, simple language. Reply in same language as user. No 18+."
    def call(url, key, mod):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json={"model": mod, "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": text}]}, timeout=12)
            return r.json()['choices'][0]['message']['content']
        except: return None
    return call("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, "llama-3.1-8b-instant") or \
           call("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "liquid/lfm-2.5-1.2b-instruct:free") or "Net issue hai baby! 😴"

# --- Admin Tools ---
async def purge_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if m.status not in ['administrator', 'creator']: return
    if not u.message.reply_to_message: return await u.message.reply_text("Reply to a message to purge!")
    for i in range(u.message.reply_to_message.message_id, u.message.message_id + 1):
        try: await c.bot.delete_message(u.effective_chat.id, i)
        except: continue

async def lock_control(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if m.status not in ['administrator', 'creator']: return
    lock = u.message.text.split()[0].endswith("lock")
    await c.bot.set_chat_permissions(u.effective_chat.id, ChatPermissions(can_send_messages=not lock))
    await u.message.reply_text(f"{'🔒 Locked' if lock else '🔓 Unlocked'} successfully! 💋")

# --- Tic-Tac-Toe AI Logic ---
def check_win(b):
    for r in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]:
        if b[r[0]] == b[r[1]] == b[r[2]] != "-": return b[r[0]]
    return "Draw" if "-" not in b else None

def get_bot_move(b):
    for sym in ["O", "X"]:
        for i in range(9):
            if b[i] == "-":
                b[i] = sym
                if check_win(b) == sym: b[i] = "-"; return i
                b[i] = "-"
    if b[4] == "-": return 4
    empty = [i for i, v in enumerate(b) if v == "-"]
    return random.choice(empty) if empty else None

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid, cid = str(u.effective_user.id), str(u.effective_chat.id)
    reply = u.message.reply_to_message
    is_bot = not (reply and not reply.from_user.is_bot)
    p2_id, p2_n = (str(c.bot.id), "Miko 👸") if is_bot else (str(reply.from_user.id), reply.from_user.first_name)
    games[cid] = {'b': ["-"]*9, 'p': {uid: "X", p2_id: "O"}, 'turn': uid, 'names': {uid: u.effective_user.first_name, p2_id: p2_n}, 'allowed': [uid, p2_id], 'is_bot': is_bot}
    kb = [[InlineKeyboardButton("⬜" if games[cid]['b'][i+j]=="-" else ("🟥" if games[cid]['b'][i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)] for i in range(0, 9, 3)]
    await u.message.reply_text(f"🎮 <b>Match: {u.effective_user.first_name} vs {p2_n}</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def callback_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; d = q.data; uid, cid = str(q.from_user.id), str(q.message.chat.id)
    if d.startswith("tt_"):
        if cid not in games or uid not in games[cid]['allowed']: return
        g = games[cid]; idx = int(d.split("_")[1])
        if uid != g['turn'] or g['b'][idx] != "-": return
        g['b'][idx] = "X"; winner = check_win(g['b'])
        if not winner and g['is_bot']:
            bm = get_bot_move(g['b'])
            if bm is not None: g['b'][bm] = "O"
            winner = check_win(g['b'])
        elif not winner: g['turn'] = [p for p in g['allowed'] if p != uid][0]
        kb = [[InlineKeyboardButton("⬜" if g['b'][i+j]=="-" else ("🟥" if g['b'][i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)] for i in range(0, 9, 3)]
        if winner:
            msg = f"🎉 Winner: {g['names'].get(uid if winner=='X' else str(c.bot.id))}!" if winner != "Draw" else "🤝 Draw!"
            await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb)); del games[cid]
        else: await q.edit_message_text(f"👉 Turn: {g['names'][g['turn']]}", reply_markup=InlineKeyboardMarkup(kb))

# --- Global Logic ---
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
    if cid not in seen_users: seen_users[cid] = {}
    seen_users[cid][uid] = {"n": u.effective_user.first_name, "id": u.effective_user.id}
    counts[cid] = counts.get(cid, 0) + 1
    if counts[cid] % 6 == 0:
        try: await u.message.set_reaction(reaction=random.choice(["🔥", "💋", "✨", "❤️"]))
        except: pass
    save_data()
    if "miko" in u.message.text.lower() or (u.message.reply_to_message and u.message.reply_to_message.from_user.id == c.bot.id):
        await u.message.reply_text(await get_miko_chat(u.message.text))

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cmd = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    cid = str(u.effective_chat.id)
    users = list(seen_users.get(cid, {}).values())
    if len(users) < (2 if cmd == "couple" else 1): return
    day = datetime.now().strftime("%y-%m-%d")
    m = random.sample(users, 2) if cmd == "couple" else [random.choice(users)]
    ids = "-".join(sorted([str(x['id']) for x in m]))
    key = f"{cid}:{cmd}:{ids}"
    if key not in locks or locks[key]['date'] != day:
        res = random.choice(MAPPING[cmd]).format(u=m[0]['n'], u1=m[0]['n'], u2=m[-1]['n'], p=random.randint(1,100))
        locks[key] = {"date": day, "res": res}; save_data()
    await u.message.reply_text(f"{locks[key]['res']}\n<i>(Fixed for 24h)</i>", parse_mode=ParseMode.HTML)

async def main():
    await startServer()
    app = TGApp.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("purge", purge_handler))
    app.add_handler(CommandHandler(["lock", "unlock"], lock_control))
    for cmd in MAPPING.keys(): app.add_handler(CommandHandler(cmd, fun_dispatcher))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    await app.initialize(); await app.start(); await app.updater.start_polling(drop_pending_updates=True); await asyncio.Event().wait()

if __name__ == "__main__": asyncio.run(main())

