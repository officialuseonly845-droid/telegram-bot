import os, logging, random, json, asyncio, requests
from datetime import datetime
from aiohttp.web import Application, AppRunner, TCPSite, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("MikoBot")

# --- Render/Uptime Server Script ---
PORT = int(os.environ.get("PORT", 8080))
ADVICE = "Fuck excuses, keep fucking going, learn from every fuck up, build your fucking life."

async def checkHealth(request):
    return Response(text=ADVICE, content_type="text/plain")

async def startServer() -> None:
    app = Application()
    app.router.add_get('/', checkHealth)
    app.router.add_get('/healthz', checkHealth)
    runner = AppRunner(app, access_log=None)
    await runner.setup()
    site = TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"✅ HTTP Server listening on port {PORT}")

# --- Config & Data ---
TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]

DATA_FILE = "miko_master.json"
def load_data():
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {"seen": {}, "locks": {}}

data_store = load_data()
seen_users, locks = data_store["seen"], data_store["locks"]
games = {}

def save_data():
    with open(DATA_FILE, 'w') as f: json.dump(data_store, f)

# --- 10 Hot Kitty URLs ---
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

MAPPING = {
    "gay": ["🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 Diva meter: {p}% for <b>{u}</b>! ✨", "💄 Gay radar: {p}% on <b>{u}</b>! 🏳️‍🌈", "👦 <b>{u}</b> loves boys {p}%! 💖", "🍭 Sweet & Gay: <b>{u}</b> ({p}%)! 🌈", "👠 <b>{u}</b>, heels peheno! {p}% Chhamiya! 💅", "🎀 <b>{u}</b> is {p}% feminine! ✨", "🏳️‍🌈 Radar: <b>{u}</b> is {p}% Rainbow lover! 🏳️‍🌈"],
    "couple": ["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding: <b>{u1}</b> & <b>{u2}</b>! {p}% Pyar! 🔔", "🔥 Toxic: <b>{u1}</b> & <b>{u2}</b>! ☢️", "💕 Jodi: <b>{u1}</b> & <b>{u2}</b>! 🥰", "💔 Breakup Loading: <b>{u1}</b> & <b>{u2}</b>! 📉", "🔞 Oye-Hoye! <b>{u1}</b> aur <b>{u2}</b>! Kaand {p}%! 🌚", "💰 Recharge Couple: <b>{u1}</b> & <b>{u2}</b>! 💸", "🎭 Acting: <b>{u1}</b> & <b>{u2}</b>! OYO full hai! 🤡"],
    "aura": ["✨ <b>{u}</b> Aura: {p}% 👑", "🗿 Chad: <b>{u}</b> ({p}%)! 🗿", "💎 Diamond: <b>{u}</b> ({p}%)! ✨", "💀 Negative: <b>{u}</b> ({p}%)! 🧼", "🔥 Gangster: <b>{u}</b> ({p}%)! 🔫", "🌈 Cute: <b>{u}</b> ({p}%)! 🧸", "📉 Noob: <b>{u}</b> ({p}%)! 🤡", "🌟 God: <b>{u}</b> ({p}%)! 🙏"]
}

# --- Global Error Handler ---
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"⚠️ Exception while handling an update: {context.error}")

# --- AI Core ---
async def get_miko_chat(text):
    sys_p = "Name: Miko. Identity: REAL girl, 22yo. Never say AI/robot. Sassy, witty. Use Hinglish. On 18+ use 🌚."
    def call(url, key, mod):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json={"model": mod, "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": text}]}, timeout=10)
            return r.json()['choices'][0]['message']['content']
        except: return None
    return call("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "liquid/lfm-40b") or call("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, "mixtral-8x7b-32768") or "Abe yaar, server ro raha hai! 😴"

# --- Handlers ---
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cmd = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    cid = str(u.effective_chat.id)
    users = list(seen_users.get(cid, {}).values())
    if not users: return await u.message.reply_text("Group mein bakchodi shuru karo pehle! 🤡")
    
    day = datetime.now().strftime("%Y-%m-%d")
    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        ids = "-".join(sorted([str(m[0]['id']), str(m[1]['id'])]))
        key = f"{cid}:{cmd}:{ids}"
        if key not in locks or locks[key]['date'] != day:
            res = random.choice(MAPPING[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
            locks[key] = {"date": day, "res": res}; save_data()
        await u.message.reply_text(f"✨ {locks[key]['res']}\n<i>(Fixed for 24h)</i>", parse_mode=ParseMode.HTML)
    else:
        m = random.choice(users); key = f"{cid}:{cmd}:{m['id']}"
        if key not in locks or locks[key]['date'] != day:
            res = random.choice(MAPPING[cmd]).format(u=m['n'], p=random.randint(0, 100))
            locks[key] = {"date": day, "res": res}; save_data()
        await u.message.reply_text(f"✨ {locks[key]['res']}\n<i>(Fixed for 24h)</i>", parse_mode=ParseMode.HTML)

async def callback_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; d = q.data; uid = str(q.from_user.id); cid = str(q.message.chat.id)
    if d.startswith("tt_"):
        if cid not in games or uid not in games[cid]['allowed']: return await q.answer("Ara bhai ye tera match nhi ha... 💋", show_alert=True)
        g = games[cid]; idx = int(d.split("_")[1])
        if uid != g['turn'] or g['b'][idx] != "-": return await q.answer("Intezar kar baby! ✨")
        await q.answer(); g['b'][idx] = g['p'][uid]
        nxt = [p for p in g['allowed'] if p != uid][0]; g['turn'] = nxt
        kb = [[InlineKeyboardButton("⬜" if g['b'][i+j]=="-" else ("🟥" if g['b'][i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)] for i in range(0, 9, 3)]
        await q.edit_message_text(f"👉 Baari: <b>{g['names'][nxt]}</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    elif d == "kt_next":
        await q.answer("Nayi photo! 💋")
        await q.edit_message_media(InputMediaPhoto(random.choice(HOT_KITTIES), caption="🐱 Sexy Kitty!", parse_mode=ParseMode.HTML), reply_markup=q.message.reply_markup)

async def tracker(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.effective_user or u.effective_user.is_bot: return
    cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
    if cid not in seen_users: seen_users[cid] = {}
    seen_users[cid][uid] = {"n": u.effective_user.first_name, "id": u.effective_user.id}; save_data()

async def main() -> None:
    await startServer()
    if not TOKEN: logger.error("BOT_TOKEN missing!"); return
    
    app = TGApp.builder().token(TOKEN).build()
    app.add_error_handler(global_error_handler)
    
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("kitty", lambda u,c: u.message.reply_photo(random.choice(HOT_KITTIES), caption="🐱 Sexy Kitty!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ɴᴇxᴛ 💋", callback_data="kt_next")]]))))
    for cmd in MAPPING.keys(): app.add_handler(CommandHandler(cmd, fun_dispatcher))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: asyncio.create_task(u.message.reply_text(asyncio.run(get_miko_chat(u.message.text)))) if ("miko" in u.message.text.lower() or (u.message.reply_to_message and u.message.reply_to_message.from_user.id == c.bot.id)) else None))
    app.add_handler(MessageHandler(filters.ALL, tracker), group=1)
    
    logger.info("🚀 Miko Final Boss Mode Online!")
    await app.initialize(); await app.start(); await app.updater.start_polling(drop_pending_updates=True); await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
