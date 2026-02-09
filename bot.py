import os, logging, random, html, httpx, asyncio, json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Data Persistence ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
DATA_FILE = "beluga_persistent_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: return {"daily_locks": {}, "seen_users": {}}
    return {"daily_locks": {}, "seen_users": {}}

def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump({"daily_locks": daily_locks, "seen_users": seen_users}, f)

data = load_data()
daily_locks = data["daily_locks"]
seen_users = data["seen_users"]
games, naughty_index = {}, {}

# --- Config ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
NAUGHTY_PHOTOS = ["https://docs.google.com/uc?export=download&id=1ha0a76nLE61Wkl-GTChueWzFzBzg9Evm"]

# --- Tic-Tac-Toe Logic ---
def draw_tt_board(board):
    kb = []
    for i in range(0, 9, 3):
        row = [InlineKeyboardButton("⬜" if board[i+j] == "-" else board[i+j], callback_data=f"tt_{i+j}") for j in range(3)]
        kb.append(row)
    return InlineKeyboardMarkup(kb)

def check_winner(b):
    pts = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for p in pts:
        if b[p[0]] == b[p[1]] == b[p[2]] != "-": return b[p[0]]
    return "Draw" if "-" not in b else None

# --- Handlers ---

async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id = str(update.effective_chat.id)
    today = str(datetime.now().date())

    if chat_id not in daily_locks or daily_locks[chat_id].get("date") != today:
        daily_locks[chat_id] = {"date": today, "commands": {}}

    if cmd in daily_locks[chat_id]["commands"]:
        return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]}", parse_mode=ParseMode.HTML)

    users = list(seen_users.get(chat_id, {}).values())
    if not users: return await update.message.reply_text("Pehle group mein thodi bakchodi karo tabhi list banegi! 🤡")

    # 🔥 POORE 78+ REPLIES YAHAN HAIN 🔥
    mapping = {
        "gay": [
            "🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 <b>{u}</b> is a pure Diva! {p}% ✨", "💄 Gay radar on <b>{u}</b>: {p}% 🏳️‍🌈", 
            "👠 <b>{u}</b>, slay queen! {p}% 👑", "🏳️‍🌈 <b>{u}</b> dropped heterosexuality! {p}% 📈", "🍭 Sweet & Gay: <b>{u}</b> ({p}%)",
            "🦄 Unicorn energy: {p}% for <b>{u}</b>! 🌈", "✨ <b>{u}</b> is {p}% glittery! 🏳️‍🌈", "👦 <b>{u}</b> loves boys {p}%! 💖",
            "🎀 <b>{u}</b> is {p}% feminine tonight! 💅", "🌈 Rainbow boy <b>{u}</b>: {p}%! 🍭"
        ],
        "roast": [
            "💀 <b>{u}</b> is pure garbage! 🚮", "🗑️ <b>{u}</b>'s face is a crime scene! 😭", "🤡 <b>{u}</b> dropped their only 2 brain cells! 🚫",
            "🔥 Roasted like a chicken: <b>{u}</b>! 🍗", "🚑 <b>{u}</b> needs mental help ASAP! 💨", "🧟 Zombies ignored <b>{u}</b>... no brains! 🧠",
            "📉 <b>{u}</b>'s IQ is lower than the room temperature! 🧊", "🚮 <b>{u}</b> is the reason why shampoo has instructions! 🧴",
            "💩 <b>{u}</b>'s birth certificate is an apology from the factory! 👶", "🛑 Stop talking, <b>{u}</b>, you're lowering the IQ of the group! 📉",
            "🤮 Looking at <b>{u}</b> makes me support abortion! 🚫", "🦴 <b>{u}</b> is so skinny, he uses a Cheeto as a walking stick! 🥢"
        ],
        "chammar": [
            "🚽 <b>Shakti</b> (Harpic CEO) spotted! 🧴", "🧹 <b>Shakti</b>'s mop is smarter than them! 🏆", "🧴 Perfume? Harpic Blue for <b>Shakti</b>! 🧼", 
            "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽", "🧼 <b>Shakti</b>, wash the floor! {p}% done! 🧹", "🤡 <b>Shakti</b>, the floor is still dirty! 🧼",
            "🪣 <b>Shakti</b>'s bucket list is just a bucket! 🪣", "🧴 <b>Shakti</b> drinks Harpic for breakfast! 🥛", "🧹 Olympic Mop Winner: <b>Shakti</b>! 🥇",
            "🚽 <b>Shakti</b>'s kingdom is the public urinal! 🏰", "🧼 Scrub harder <b>Shakti</b>! {p}% left! 🧼"
        ],
        "aura": [
            "✨ <b>{u}</b>'s Aura: {p}% 👑", "📉 -{p} Aura for <b>{u}</b>! 💀", "🌟 Glowing at {p}%! 🌌", "🌑 Cardboard Aura: {p}% 📦",
            "🔥 Godly Aura: <b>{u}</b> ({p}%)! ⚡", "💩 Shitty Aura: <b>{u}</b> ({p}%)! 🤢", "🗿 Chad Aura: <b>{u}</b> ({p}%)! 🗿",
            "🤡 Clown Aura: <b>{u}</b> ({p}%)! 🎪", "🌈 Rainbow Aura: <b>{u}</b> ({p}%)! 🏳️‍🌈", "💎 Diamond Aura: <b>{u}</b> ({p}%)! ✨"
        ],
        "monkey": [
            "🐒 <b>{u}</b> is {p}% Gorilla! 🦍", "🍌 Banana lover: <b>{u}</b>! 🐵", "🐒 Jungle king: <b>{u}</b>! ({p}%) 🌲",
            "🦧 <b>{u}</b> is a pure Orangutan! 🐵", "🐒 Monkey business detected from <b>{u}</b>! 🍌"
        ],
        "couple": [
            "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! {p}% 🔔", "🔥 Toxic goals: <b>{u1}</b> & <b>{u2}</b>! {p}% ☢️",
            "💕 Rab Ne Bana Di Jodi: <b>{u1}</b> & <b>{u2}</b>! ({p}%) 🥰", "💔 Breakup loading for <b>{u1}</b> & <b>{u2}</b>! {p}% 📉", "🥀 One-sided love: <b>{u1}</b> for <b>{u2}</b>! ({p}%) 😭"
        ],
        "brain": [
            "🧠 <b>{u}</b>'s Brain: {p}% 🔋", "💡 Intelligence: <b>{u}</b> ({p}%)! 🕯️", "🥔 Potato Brain: <b>{u}</b> ({p}%)! 🥔",
            "⚙️ Processing... <b>{u}</b> is {p}% slow! 🐌", "🧠 Big Brain Energy: <b>{u}</b> ({p}%)! ⚡"
        ]
    }

    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(mapping[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(mapping[cmd]).format(u=m['n'], p=random.randint(0, 100))
    
    daily_locks[chat_id]["commands"][cmd] = res
    save_data(); await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

# --- Tictac & Callback Handlers (Samina Style) ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; data, chat_id, uid = query.data, query.message.chat.id, str(query.from_user.id)
    await query.answer()

    if data.startswith("tt_"):
        if chat_id not in games or uid != games[chat_id]['turn']: return
        idx = int(data.split("_")[1]); b = games[chat_id]['board']
        if b[idx] != "-": return
        b[idx] = games[chat_id]['players'][uid]['s']; win = check_winner(b)
        
        if win:
            p = games[chat_id]['players']; ids = list(p.keys())
            txt = f"Congratulations {p[uid]['n']}! 🎉\n\n{p[ids[0]]['n']} vs {p[ids[1]]['n']}\n<b>{p[uid]['n']} wins! Well played!</b> ❤️" if win != "Draw" else "🤝 Match Draw!"
            await query.edit_message_text(txt, reply_markup=draw_tt_board(b), parse_mode=ParseMode.HTML)
            del games[chat_id]
        else:
            ids = list(games[chat_id]['players'].keys())
            next_t = ids[1] if uid == ids[0] else ids[0]; games[chat_id]['turn'] = next_t
            if games[chat_id]['vs_bot'] and next_t == str(context.bot.id):
                empty = [i for i, v in enumerate(b) if v == "-"]; b[random.choice(empty)] = "⭕"
                games[chat_id]['turn'] = uid
                await query.edit_message_text(f"Turn: {p[uid]['n']}", reply_markup=draw_tt_board(b))
            else:
                await query.edit_message_text(f"Turn: {games[chat_id]['players'][next_t]['n']}", reply_markup=draw_tt_board(b))

    elif data.startswith("ng_"):
        idx = (naughty_index.get(chat_id, 0) + 1) % len(NAUGHTY_PHOTOS) if data == "ng_next" else random.randint(0, len(NAUGHTY_PHOTOS)-1)
        naughty_index[chat_id] = idx
        await query.edit_message_media(media=InputMediaPhoto(media=NAUGHTY_PHOTOS[idx], caption=f"🔞 Photo: {idx+1}/{len(NAUGHTY_PHOTOS)}"), reply_markup=query.message.reply_markup)

# --- Tracking & Main ---
async def core_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id, user_id = str(update.effective_chat.id), str(update.effective_user.id)
    if chat_id not in seen_users: seen_users[chat_id] = {}
    seen_users[chat_id][user_id] = {"n": update.effective_user.first_name}
    save_data()

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("naughty", naughty_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    for c in ["gay", "roast", "chammar", "aura", "couple", "monkey", "brain"]: app.add_handler(CommandHandler(c, fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    app.add_handler(MessageHandler(filters.PHOTO & filters.FORWARDED, lambda u, c: u.message.reply_text(f"ID: `{u.message.photo[-1].file_id}`")))
    app.run_polling()

if __name__ == '__main__': main()

# Is part ko code ke sabse niche check karo, indentation sahi honi chahiye
def main():
    # Token check
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN set nahi hai!")
        return

    app = Application.builder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("naughty", naughty_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    for c in ["gay", "roast", "chammar", "aura", "couple", "monkey", "brain"]:
        app.add_handler(CommandHandler(c, fun_dispatcher))
        
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    app.add_handler(MessageHandler(filters.PHOTO & filters.FORWARDED, lambda u, c: u.message.reply_text(f"ID: `{u.message.photo[-1].file_id}`")))
    
    print("Beluga is starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"Bot crash ho gaya: {e}")

