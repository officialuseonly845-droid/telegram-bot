import os, logging, random, html, json, asyncio, requests
from datetime import datetime
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Error Handling ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Flask for Uptime Robot (Never Sleep) ---
server = Flask('')
@server.route('/')
def home(): return "Miko is Awake! ✨"
def run_web(): server.run(host='0.0.0.0', port=8080)

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
        except: return {"seen": {}, "daily": {}, "config": {"model": "auto"}}
    return {"seen": {}, "daily": {}, "config": {"model": "auto"}}

data_store = load_data()
seen_users, daily_locks, config = data_store["seen"], data_store["daily"], data_store["config"]
games = {}

def save_data():
    with open(DATA_FILE, 'w') as f: json.dump(data_store, f)

# --- Global Error Handler ---
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"⚠️ Miko Error: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Ofo! Kuch phat gaya peeche... par tension mat lo, Miko zinda hai! ✨")

# --- AI Logic (OpenRouter + Groq Backup) ---
async def get_miko_reply(text):
    mode = config.get("model", "auto")
    sys_p = "Name: Miko. Sassy, witty, light 18+ humor, uses light slang like 'bsdk', 'chutiye'. Hinglish. Short sentences. ✨"
    
    def call_api(url, key, model):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": text}]}, timeout=10)
            return r.json()['choices'][0]['message']['content']
        except: return None

    # Priority: OpenRouter -> Groq -> Default
    res = call_api("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "liquid/lfm-40b")
    if not res:
        res = call_api("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, "mixtral-8x7b-32768")
    return res or "Abe yaar, dimaag mat khao, dono API ro rahi hain! 😴"

# --- Savage Mapping (18+ Light) ---
MAPPING = {
    "chammar": [
        "🚽 <b>Shakti</b> (Harpic CEO) spotted! Shakal aisi ki flushing sound sunke orgasm aata hoga! 🧴",
        "🧹 Abe oh <b>Shakti</b>, Harpic ke nashedi, jaake toilet saaf kar warna gaand pe pocha padega! 🏆",
        "🪠 Sultan-e-Gutter: <b>Shakti</b>! Tu vahi hai na jo flush kharab hone pe muh se saaf karta hai? 🚽"
    ],
    "couple": [
        "🔞 Oye-Hoye! <b>{u1}</b> aur <b>{u2}</b>! {p}% chances hain ki aaj raat koi 'Kand' hoga! 🛌",
        "💕 Rab Ne Bana Di Jodi: Ek lulla toh ek lalli! <b>{u1}</b> & <b>{u2}</b>! 😂 🥰",
        "🎭 <b>{u1}</b> & <b>{u2}</b>! Acting band karo chaman chutiye, sabko pata hai OYO ki booking full hai! 🤡"
    ],
    "gay": [
        "🌈 <b>{u}</b> is {p}% GAY! Itna mat matak bsdk, sab dikh raha hai ✨🌚",
        "💄 Gay radar on <b>{u}</b>: {p}%! Piche se koi rainbow danda dene wala hai, bach ke rehna! 🏳️‍🌈",
        "👠 <b>{u}</b>, heels pehen ke thoda matak ke chalo toh! {p}% Chhamiya vibes! 💅"
    ]
}

# --- Tic-Tac-Toe Security Logic ---
def draw_tt_board(board):
    kb = []
    for i in range(0, 9, 3):
        row = [InlineKeyboardButton("⬜" if board[i+j] == "-" else ("🟥" if board[i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)]
        kb.append(row)
    return InlineKeyboardMarkup(kb)

async def tictac_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = str(update.effective_user.id), str(update.effective_chat.id)
    reply = update.message.reply_to_message
    p1_n = update.effective_user.first_name
    p2_n, p2_id = (reply.from_user.first_name, str(reply.from_user.id)) if reply and not reply.from_user.is_bot else ("Miko 🤖", str(context.bot.id))
    
    games[cid] = {'board': ["-"]*9, 'players': {uid: {"n": p1_n, "s": "X"}, p2_id: {"n": p2_n, "s": "O"}}, 'turn': uid, 'allowed': [uid, p2_id]}
    await update.message.reply_text(f"🎮 <b>{p1_n} (🟥) vs {p2_n} (🟩)</b>\n👉 Baari: {p1_n}", reply_markup=draw_tt_board(games[cid]['board']), parse_mode=ParseMode.HTML)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d, uid, cid = q.data, str(q.from_user.id), str(q.message.chat.id)
    if d.startswith("tt_"):
        if cid not in games or uid not in games[cid]['allowed']:
            return await q.answer("❌ Abe chutiye, ye tera game nahi hai! 🤡", show_alert=True)
        g = games[cid]; b = g['board']; idx = int(d.split("_")[1])
        if uid != g['turn'] or b[idx] != "-": return await q.answer("Ruk ja gadhe! ✨")
        await q.answer(); b[idx] = g['players'][uid]['s']
        # Winner logic here... (Simplified for code length)
        await q.edit_message_text(f"👉 Next Turn...", reply_markup=draw_tt_board(b))

async def main():
    # Start Web Server for Uptime Robot
    Thread(target=run_web).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_error_handler(global_error_handler)
    
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("kitty", lambda u, c: u.message.reply_photo(KITTY_PHOTOS[0], "🐱 <b>Meow! Sexy Kitty!</b>", parse_mode=ParseMode.HTML)))
    
    for c in MAPPING.keys(): app.add_handler(CommandHandler(c, fun_dispatcher))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply))
    
    print("Miko is Online & Never Sleeping! 🚀")
    await app.initialize(); await app.start_polling(); await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
