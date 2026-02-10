import os, logging, random, html, json, asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Logging & Persistence ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

DATA_FILE = "miko_data.json"
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: return {"daily_locks": {}, "seen_users": {}, "cooldowns": {}}
    return {"daily_locks": {}, "seen_users": {}, "cooldowns": {}}

def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump({"daily_locks": daily_locks, "seen_users": seen_users, "cooldowns": cooldown_list}, f)

data_store = load_data()
daily_locks = data_store.get("daily_locks", {})
seen_users = data_store.get("seen_users", {})
cooldown_list = data_store.get("cooldowns", {})
games, kitty_index = {}, {}

# --- Config ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
KITTY_PHOTOS = ["https://i.postimg.cc/8kKLbdqh/IMG-20260209-195025-385.jpg"]

# --- Tic-Tac-Toe Logic ---
def draw_tt_board(board):
    kb = []
    for i in range(0, 9, 3):
        row = [InlineKeyboardButton("⬜" if board[i+j]=="-" else ("🟥" if board[i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)]
        kb.append(row)
    return InlineKeyboardMarkup(kb)

def get_bot_move(b):
    win_pts = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for s in ["O", "X"]:
        for p in win_pts:
            vals = [b[p[0]], b[p[1]], b[p[2]]]
            if vals.count(s) == 2 and vals.count("-") == 1: return p[vals.index("-")]
    empty = [i for i, v in enumerate(b) if v == "-"]
    return random.choice(empty) if empty else None

# --- Handlers ---
async def tictac_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, chat_id = str(update.effective_user.id), str(update.effective_chat.id)
    
    if user_id in cooldown_list:
        last = datetime.fromisoformat(cooldown_list[user_id])
        if datetime.now() < last + timedelta(minutes=10):
            wait = int(((last + timedelta(minutes=10)) - datetime.now()).seconds / 60)
            return await update.message.reply_text(f"⏳ <b>Miko says:</b> Sabar kar! {wait} min baad khelna. 🛑", parse_mode=ParseMode.HTML)

    reply = update.message.reply_to_message
    p1_id, p1_name = user_id, update.effective_user.first_name
    is_vs_bot = False if (reply and not reply.from_user.is_bot) else True
    p2_name = reply.from_user.first_name if not is_vs_bot else "Miko 🤖"
    p2_id = str(reply.from_user.id) if not is_vs_bot else str(context.bot.id)

    games[chat_id] = {'board': ["-"]*9, 'players': {p1_id: {"n": p1_name, "s": "X"}, p2_id: {"n": p2_name, "s": "O"}}, 'turn': p1_id, 'vs_bot': is_vs_bot, 'allowed': [p1_id, p2_id]}
    
    header = f"🎮 <b>━━━━━━━━━━━━━━━━━━</b>\n🔥 <b>{p1_name}</b> (🟥) <b>VS</b> <b>{p2_name}</b> (🟩)\n<b>━━━━━━━━━━━━━━━━━━</b>\n👉 <b>Turn: {p1_name}</b>"
    await update.message.reply_text(header, reply_markup=draw_tt_board(games[chat_id]['board']), parse_mode=ParseMode.HTML)

async def kitty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    kitty_index[chat_id] = 0
    kb = [[InlineKeyboardButton("Next 🌸", callback_data="kt_next"), InlineKeyboardButton("Refresh 🍎", callback_data="kt_ref")]]
    await update.message.reply_photo(photo=KITTY_PHOTOS[0], caption="🐱 <b>Miko's Kitty Gallery</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id, today = str(update.effective_chat.id), str(datetime.now().date())
    if chat_id not in daily_locks or daily_locks[chat_id].get("date") != today: daily_locks[chat_id] = {"date": today, "commands": {}}
    if cmd in daily_locks[chat_id]["commands"]: return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]}", parse_mode=ParseMode.HTML)
    users = list(seen_users.get(chat_id, {}).values())
    if not users: return await update.message.reply_text("Pehle chat toh karo noobs! 🤡")

    mapping = {
        "gay": ["🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 <b>{u}</b> Diva meter: {p}%! ✨", "💄 Gay radar: <b>{u}</b> ({p}%) 🏳️‍🌈"],
        "roast": ["💀 <b>{u}</b> is pure garbage! 🚮", "🗑️ <b>{u}</b>'s face is a crime scene! 😭", "🤡 0 brain cells in <b>{u}</b>!"],
        "chammar": ["🚽 <b>Shakti</b> (Harpic CEO) spotted! 🧴", "🧹 <b>Shakti</b>'s mop is smarter! 🏆", "🪠 Sultan of Sewage: <b>Shakti</b>! 🚽"],
        "aura": ["✨ <b>{u}</b>'s Aura: {p}% 👑", "🗿 Chad Aura: <b>{u}</b> ({p}%)! 🗿"],
        "couple": ["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💕 Rab Ne Bana Di Jodi: <b>{u1}</b> & <b>{u2}</b>! ({p}%) 🥰"],
        "monkey": ["🐒 <b>{u}</b> is {p}% Gorilla! 🦍", "🍌 Banana lover: <b>{u}</b>! 🐵"],
        "brain": ["🧠 <b>{u}</b>'s Brain: {p}% 🔋", "🥔 Potato Brain: <b>{u}</b>! 🥔"]
    }
    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(mapping[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(mapping[cmd]).format(u=m['n'], p=random.randint(0, 100))
    daily_locks[chat_id]["commands"][cmd] = res
    save_data(); await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

async def auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    bot_un = (await context.bot.get_me()).username.lower()
    text = update.message.text.lower()
    if f"@{bot_un}" in text or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
        roasts = ["Kyun thak rahe ho? 🥱", "Miko busy hai, baad mein aana. 💅", "Tujhse baat karne ka tax lagta hai. 💸", "Tera IQ dekh ke meri battery low ho gayi. 🔋"]
        await update.message.reply_text(f"✨ {random.choice(roasts)}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; data, chat_id, uid = query.data, str(query.message.chat.id), str(query.from_user.id)
    if data.startswith("kt_"):
        await query.answer()
        idx = (kitty_index.get(chat_id, 0) + 1) % len(KITTY_PHOTOS) if data == "kt_next" else random.randint(0, len(KITTY_PHOTOS)-1)
        kitty_index[chat_id] = idx
        await query.edit_message_media(InputMediaPhoto(KITTY_PHOTOS[idx], caption=f"🐱 Photo: {idx+1}"), reply_markup=query.message.reply_markup)
    elif data.startswith("tt_"):
        if chat_id not in games or uid not in games[chat_id]['allowed']: return await query.answer("Ye tera game nahi hai! 🤡", show_alert=True)
        g = games[chat_id]; b = g['board']
        if uid != g['turn']: return await query.answer("Teri baari nahi hai! 🛑")
        idx = int(data.split("_")[1])
        if b[idx] != "-": return await query.answer("Bhara hua hai!")
        await query.answer(); b[idx] = g['players'][uid]['s']
        win = check_winner(b)
        if win:
            res = f"<b>{g['players'][uid]['n']} Won! 🎉</b>" if win != "Draw" else "<b>Draw! 🤝</b>"
            for p in g['allowed']: 
                if p != str(context.bot.id): cooldown_list[p] = datetime.now().isoformat()
            save_data()
            await query.edit_message_text(f"🏁 <b>GAME OVER</b>\n\n{res}", reply_markup=draw_tt_board(b), parse_mode=ParseMode.HTML); del games[chat_id]
        else:
            p_ids = list(g['players'].keys())
            nxt = p_ids[1] if uid == p_ids[0] else p_ids[0]; g['turn'] = nxt
            if g['vs_bot'] and nxt == str(context.bot.id):
                move = get_bot_move(b)
                if move is not None: b[move] = "O"
                if check_winner(b):
                    for p in g['allowed']: 
                        if p != str(context.bot.id): cooldown_list[p] = datetime.now().isoformat()
                    save_data()
                    await query.edit_message_text("<b>Miko Won! hehe 😄</b>", reply_markup=draw_tt_board(b), parse_mode=ParseMode.HTML); del games[chat_id]
                else:
                    g['turn'] = uid
                    await query.edit_message_text(f"👉 <b>Turn: {g['players'][uid]['n']}</b>", reply_markup=draw_tt_board(b), parse_mode=ParseMode.HTML)
            else:
                await query.edit_message_text(f"👉 <b>Turn: {g['players'][nxt]['n']}</b>", reply_markup=draw_tt_board(b), parse_mode=ParseMode.HTML)

def check_winner(b):
    win_pts = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for p in win_pts:
        if b[p[0]] == b[p[1]] == b[p[2]] != "-": return b[p[0]]
    return "Draw" if "-" not in b else None

async def tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    cid, uid = str(update.effective_chat.id), str(update.effective_user.id)
    if cid not in seen_users: seen_users[cid] = {}
    seen_users[cid][uid] = {"n": html.escape(update.effective_user.first_name)}; save_data()

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("kitty", kitty_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    for c in ["gay", "roast", "chammar", "aura", "couple", "monkey", "brain"]: app.add_handler(CommandHandler(c, fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply))
    app.add_handler(MessageHandler(filters.ALL, tracker), group=1)
    print("Miko is Online! 🔥")
    app.run_polling()

if __name__ == '__main__': main()
