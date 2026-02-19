import os, logging, random, json, asyncio, requests
from datetime import datetime, timedelta
from aiohttp.web import Application, AppRunner, TCPSite, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ==========================================
# PART 1: SYSTEM & DATABASE (Miko's Brain)
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
DATA_FILE = "miko_brain.json"
spam_tracker = {}
games = {}

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"seen": {}, "locks": {}, "counts": {}, "inactive": []}

db = load_db()
def save_db():
    with open(DATA_FILE, 'w') as f: json.dump(db, f, indent=4)

# ==========================================
# PART 2: SASSY REPLIES (7 Each - Fixed 24h)
# ==========================================
MAPPING = {
    "gay": [
        "🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 Diva radar: {p}% for <b>{u}</b>! ✨", 
        "💄 Gay meter: {p}% on <b>{u}</b>! 🏳️‍🌈", "👠 <b>{u}</b> is {p}% Chhamiya! 💅",
        "🫦 <b>{u}</b> is {p}% bottom material! 🍑", "🎀 {p}% Girly vibes from <b>{u}</b>! 💅",
        "🦄 <b>{u}</b> is {p}% Rainbow lover! 🍭"
    ],
    "couple": [
        "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding: <b>{u1}</b> & <b>{u2}</b>! {p}% Pyar! 🔔",
        "🔥 Toxic match: <b>{u1}</b> & <b>{u2}</b>! ☢️", "💕 Jodi: <b>{u1}</b> & <b>{u2}</b>! 🥰",
        "🔞 Kaand {p}% between <b>{u1}</b> & <b>{u2}</b>! 🌚", "🎭 Acting: <b>{u1}</b> & <b>{u2}</b>! OYO full hai! 🤡",
        "💔 Breakup Loading: <b>{u1}</b> & <b>{u2}</b>! 📉"
    ],
    "aura": [
        "✨ <b>{u}</b> Aura: {p}% 👑", "🗿 Chad: <b>{u}</b> ({p}%)! 🗿", "💎 Diamond: <b>{u}</b> ({p}%)! ✨",
        "🦁 Sher: <b>{u}</b> ({p}%)! 👑", "🔥 Gangster: <b>{u}</b> ({p}%)! 🔫", "🌟 God Level: <b>{u}</b> ({p}%)! 🙏",
        "📉 Negative Aura: <b>{u}</b> ({p}%)! 🧼"
    ]
}

# ==========================================
# PART 3: ADMIN TOOLS (Mute, Ban, Unmute, Purge)
# ==========================================
def parse_time(t_str):
    unit = t_str[-1].lower()
    try:
        val = int(t_str[:-1])
        if unit == 'm': return timedelta(minutes=val)
        if unit == 'h': return timedelta(hours=val)
        if unit == 'd': return timedelta(days=val)
    except: return None

async def admin_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if m.status not in ['administrator', 'creator']: return
    cmd_parts = u.message.text.split()
    cmd = cmd_parts[0].lower(); target_id = None; un = "User"

    if u.message.reply_to_message:
        target_id = u.message.reply_to_message.from_user.id
        un = u.message.reply_to_message.from_user.first_name
    elif len(cmd_parts) > 1 and cmd_parts[1].startswith('@'):
        username = cmd_parts[1].replace('@', '')
        for cid in db["seen"]:
            for uid in db["seen"][cid]:
                if db["seen"][cid][uid].get('un') == username:
                    target_id = int(uid); un = db["seen"][cid][uid]['n']; break
    
    if not target_id: return 

    if "/unmute" in cmd:
        await c.bot.restrict_chat_member(u.effective_chat.id, target_id, permissions=ChatPermissions(can_send_messages=True))
        await u.message.reply_text(f"🔓 Okay, {un} ab bol sakta hai.")
    else:
        t_str = cmd_parts[2] if len(cmd_parts) > 2 else "15m"
        dur = parse_time(t_str)
        if not dur: return
        until = datetime.now() + dur
        if "/mute" in cmd:
            await c.bot.restrict_chat_member(u.effective_chat.id, target_id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
            await u.message.reply_text(f"🔇 Shant! {un} ko {t_str} ke liye shant kar diya.")
        elif "/ban" in cmd:
            await c.bot.ban_chat_member(u.effective_chat.id, target_id, until_date=until)
            await u.message.reply_text(f"🚫 {un} gaya tel lene {t_str} tak!")

async def fast_purge(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if m.status not in ['administrator', 'creator'] or not u.message.reply_to_message: return
    msg_ids = list(range(u.message.reply_to_message.message_id, u.message.message_id + 1))
    for i in range(0, len(msg_ids), 100):
        try: await c.bot.delete_messages(u.effective_chat.id, msg_ids[i:i+100])
        except: pass

# ==========================================
# PART 4: ANTI-SPAM & MONITOR
# ==========================================
async def anti_spam_check(u: Update):
    uid = u.effective_user.id; now = datetime.now()
    if uid not in spam_tracker: spam_tracker[uid] = []
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= 3:
        try:
            await u.message.delete()
            if len(spam_tracker[uid]) == 3:
                await u.message.reply_text("Spam mt kr laadle mute 🔇 ho jayega faltu ma 😮‍💨")
        except: pass
        return True
    return False

async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    if await anti_spam_check(u): return
    cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
    if cid in [str(x) for x in db.get("inactive", [])]: return
    if cid not in db["seen"]: db["seen"][cid] = {}
    db["seen"][cid][uid] = {"un": u.effective_user.username, "n": u.effective_user.first_name}
    save_db()
    if "miko" in u.message.text.lower() or (u.message.reply_to_message and u.message.reply_to_message.from_user.id == c.bot.id):
        sys_p = "Your name is Miko, 22yo bold girl. Sassy, funny roasts. Light 18+ humor allowed. Reply in Hinglish."
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {os.environ.get('GROQ_API_KEY')}"}, json={"model": "llama-3.1-8b-instant", "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": u.message.text}]}, timeout=10)
            await u.message.reply_text(r.json()['choices'][0]['message']['content'])
        except: await u.message.reply_text("Mood nahi hai abhi! 🥱")

# ==========================================
# PART 5: GAMES & PERSISTENCE (24H Fixed)
# ==========================================
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = str(u.effective_chat.id)
    cmd = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    users = list(db["seen"].get(cid, {}).values())
    if len(users) < (2 if cmd == "couple" else 1): return
    day = datetime.now().strftime("%y-%m-%d"); lock_key = f"{cid}:{cmd}"
    if lock_key in db.get("locks", {}) and db["locks"][lock_key]["date"] == day:
        res = db["locks"][lock_key]["res"]
    else:
        m = random.sample(users, 2) if cmd == "couple" else [random.choice(users)]
        res = random.choice(MAPPING[cmd]).format(u=m[0]['n'], u1=m[0]['n'], u2=m[-1]['n'], p=random.randint(1,100))
        if "locks" not in db: db["locks"] = {}
        db["locks"][lock_key] = {"date": day, "res": res}; save_db()
    await u.message.reply_text(f"{res}\n<i>(Fixed for 24h)</i>", parse_mode=ParseMode.HTML)

# ==========================================
# PART 6: TIC-TAC-TOE (AI)
# ==========================================
async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = str(u.effective_chat.id); uid = str(u.effective_user.id)
    games[cid] = {'b': ["-"]*9, 'p': {uid: "X", "bot": "O"}, 'turn': uid}
    kb = [[InlineKeyboardButton("⬜", callback_data=f"tt_{i+j}") for j in range(3)] for i in range(0, 9, 3)]
    await u.message.reply_text(f"🎮 Match Started! You are 🟥", reply_markup=InlineKeyboardMarkup(kb))

async def callback_tt(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; cid = str(q.message.chat.id); uid = str(q.from_user.id)
    if cid not in games or uid != games[cid]['turn']: return
    idx = int(q.data.split("_")[1]); g = games[cid]
    if g['b'][idx] != "-": return
    g['b'][idx] = "X"
    # Basic Bot Move logic
    empty = [i for i, v in enumerate(g['b']) if v == "-"]
    if empty: g['b'][random.choice(empty)] = "O"
    kb = [[InlineKeyboardButton("⬜" if g['b'][i+j]=="-" else ("🟥" if g['b'][i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)] for i in range(0, 9, 3)]
    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

# ==========================================
# PART 7: RUNNER (Uptime Server + Bot)
# ==========================================
async def start_server():
    app = Application(); app.router.add_get('/', lambda r: Response(text="Miko Live!"))
    runner = AppRunner(app); await runner.setup()
    await TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080))).start()

async def main():
    await start_server()
    app = TGApp.builder().token(os.environ.get("BOT_TOKEN")).build()
    app.add_handler(CommandHandler(["gay", "couple", "aura"], fun_dispatcher))
    app.add_handler(CommandHandler(["mute", "unmute", "ban"], admin_handler))
    app.add_handler(CommandHandler("purge", fast_purge))
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CallbackQueryHandler(callback_tt, pattern="^tt_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    await app.initialize(); await app.start(); await app.updater.start_polling(); await asyncio.Event().wait()

if __name__ == "__main__": asyncio.run(main())
