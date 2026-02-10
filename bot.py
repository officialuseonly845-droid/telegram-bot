import os, logging, random, html, json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Persistence ---
DATA_FILE = "beluga_final_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: return {"daily_locks": {}, "seen_users": {}}
    return {"daily_locks": {}, "seen_users": {}}

def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump({"daily_locks": daily_locks, "seen_users": seen_users}, f)

data_store = load_data()
daily_locks = data_store.get("daily_locks", {})
seen_users = data_store.get("seen_users", {})
games, kitty_index = {}, {}

# --- Config ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") # Env variable se token
KITTY_PHOTOS = ["https://i.postimg.cc/8kKLbdqh/IMG-20260209-195025-385.jpg"]

# --- Tic-Tac-Toe Logic (Level 3 Smart Bot) ---
def draw_tt_board(board):
    kb = []
    for i in range(0, 9, 3):
        row = []
        for j in range(3):
            val = board[i+j]
            # White initial, Red X, Green O
            char = "⬜" if val == "-" else ("❌" if val == "X" else "⭕")
            row.append(InlineKeyboardButton(char, callback_data=f"tt_{i+j}"))
        kb.append(row)
    return InlineKeyboardMarkup(kb)

def check_winner(b):
    win_pts = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for p in win_pts:
        if b[p[0]] == b[p[1]] == b[p[2]] != "-": return b[p[0]]
    return "Draw" if "-" not in b else None

def get_bot_move(b):
    win_pts = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    # Win (O) ya Block (X)
    for s in ["O", "X"]:
        for p in win_pts:
            vals = [b[p[0]], b[p[1]], b[p[2]]]
            if vals.count(s) == 2 and vals.count("-") == 1:
                return p[vals.index("-")]
    empty = [i for i, v in enumerate(b) if v == "-"]
    return random.choice(empty) if empty else None

# --- Command Handlers ---

async def kitty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    kitty_index[chat_id] = 0
    kb = [[InlineKeyboardButton("Next ➡️ 🌸", callback_data="kt_next"), 
           InlineKeyboardButton("Refresh 🔃 🍎", callback_data="kt_ref")]]
    await update.message.reply_photo(photo=KITTY_PHOTOS[0], caption="🐱 **Beluga Kitty Gallery**", 
                                   reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def tictac_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, user, reply = str(update.effective_chat.id), update.effective_user, update.message.reply_to_message
    p1_id, p1_name = str(user.id), user.first_name
    is_vs_bot = False if (reply and not reply.from_user.is_bot) else True
    p2_id, p2_name = (str(reply.from_user.id), reply.from_user.first_name) if not is_vs_bot else (str(context.bot.id), "Beluga 🤖")
    
    games[chat_id] = {
        'board': ["-"]*9, 
        'players': {p1_id: {"n": p1_name, "s": "X"}, p2_id: {"n": p2_name, "s": "O"}}, 
        'turn': p1_id, 'vs_bot': is_vs_bot
    }
    msg = f"🎮 **Tic-Tac-Toe**\n\n❤️ {p1_name} (❌) **VS** 💙 {p2_name} (⭕)\n\n👉 Turn: {p1_name}"
    await update.message.reply_text(msg, reply_markup=draw_tt_board(games[chat_id]['board']), parse_mode=ParseMode.MARKDOWN)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; data, chat_id, uid = query.data, str(query.message.chat.id), str(query.from_user.id)
    await query.answer()

    if data.startswith("kt_"):
        idx = (kitty_index.get(chat_id, 0) + 1) % len(KITTY_PHOTOS) if data == "kt_next" else random.randint(0, len(KITTY_PHOTOS)-1)
        kitty_index[chat_id] = idx
        await query.edit_message_media(media=InputMediaPhoto(media=KITTY_PHOTOS[idx], caption=f"🐱 Photo: {idx+1}/{len(KITTY_PHOTOS)}"),
                                       reply_markup=query.message.reply_markup)

    elif data.startswith("tt_"):
        if chat_id not in games or uid != games[chat_id]['turn']: return
        g = games[chat_id]; b = g['board']; idx = int(data.split("_")[1])
        if b[idx] != "-": return
        b[idx] = g['players'][uid]['s']
        
        win = check_winner(b)
        if win:
            res = f"Congrats {g['players'][uid]['n']}! You won! 🎉" if win == "X" else "I won this time hehe 😄"
            if win == "Draw": res = "🤝 It's a Draw!"
            await query.edit_message_text(f"🏁 **Game Over**\n\n{res}", reply_markup=draw_tt_board(b)); del games[chat_id]
        else:
            p_ids = list(g['players'].keys())
            nxt = p_ids[1] if uid == p_ids[0] else p_ids[0]; g['turn'] = nxt
            if g['vs_bot'] and nxt == str(context.bot.id):
                b[get_bot_move(b)] = "O"
                if check_winner(b):
                    await query.edit_message_text("🏁 **Game Over**\n\nI won this time hehe 😄", reply_markup=draw_tt_board(b)); del games[chat_id]
                else:
                    g['turn'] = uid
                    await query.edit_message_text(f"👉 Turn: {g['players'][uid]['n']}", reply_markup=draw_tt_board(b))
            else:
                await query.edit_message_text(f"👉 Turn: {g['players'][nxt]['n']}", reply_markup=draw_tt_board(b))

# --- Dispatcher (Savage Replies) ---
async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id, today = str(update.effective_chat.id), str(datetime.now().date())

    if chat_id not in daily_locks or daily_locks[chat_id].get("date") != today:
        daily_locks[chat_id] = {"date": today, "commands": {}}
    if cmd in daily_locks[chat_id]["commands"]:
        return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]}", parse_mode=ParseMode.HTML)

    users = list(seen_users.get(chat_id, {}).values())
    if not users: return await update.message.reply_text("List khali hai! Thodi chat karo pehle. 🤡")

    mapping = {
        "gay": ["🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 <b>{u}</b> Diva meter: {p}%! ✨", "💄 Gay radar: <b>{u}</b> ({p}%) 🏳️‍🌈", "👠 <b>{u}</b>, slay queen! {p}% 👑", "🍭 Sweet & Gay: <b>{u}</b> ({p}%)", "🏳️‍🌈 Proudly Gay: <b>{u}</b> ({p}%)"],
        "roast": ["💀 <b>{u}</b> is pure garbage! 🚮", "🗑️ <b>{u}</b>'s face is a crime scene! 😭", "🤡 <b>{u}</b> has 0 brain cells! 🚫", "🔥 Roasted like a chicken: <b>{u}</b>! 🍗", "📉 IQ lower than room temperature: <b>{u}</b>! 🧊", "🚮 {u} is why shampoo has instructions! 🧴"],
        "chammar": ["🚽 <b>Shakti</b> (Harpic CEO) spotted! 🧴", "🧹 <b>Shakti</b>'s mop is smarter! 🏆", "🪠 Sultan of Sewage: <b>Shakti</b>! 🚽", "🧴 Perfume? Harpic Blue for <b>Shakti</b>! 🧼"],
        "aura": ["✨ <b>{u}</b>'s Aura: {p}% 👑", "📉 -{p} Aura for <b>{u}</b>! 💀", "🗿 Chad Aura: <b>{u}</b> ({p}%)! 🗿", "🤡 Clown Aura: <b>{u}</b> ({p}%)! 🎪"],
        "couple": ["💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! {p}% 🔔", "💕 Rab Ne Bana Di Jodi: <b>{u1}</b> & <b>{u2}</b>! ({p}%) 🥰"],
        "monkey": ["🐒 <b>{u}</b> is {p}% Gorilla! 🦍", "🍌 Banana lover: <b>{u}</b>! 🐵", "🐒 Jungle king: <b>{u}</b>! ({p}%) 🌲"],
        "brain": ["🧠 <b>{u}</b>'s Brain: {p}% 🔋", "💡 Intelligence: <b>{u}</b> ({p}%)! 🕯️", "🥔 Potato Brain: <b>{u}</b> ({p}%)! 🥔"]
    }

    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(mapping[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(mapping[cmd]).format(u=m['n'], p=random.randint(0, 100))
    
    daily_locks[chat_id]["commands"][cmd] = res
    save_data(); await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

async def core_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: return
    chat_id, user_id = str(update.effective_chat.id), str(update.effective_user.id)
    if chat_id not in seen_users: seen_users[chat_id] = {}
    seen_users[chat_id][user_id] = {"n": html.escape(update.effective_user.first_name)}
    save_data()

# --- Main ---
def main():
    if not TOKEN: print("Bhai env variable TELEGRAM_BOT_TOKEN nahi mila!"); return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("kitty", kitty_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    for c in ["gay", "roast", "chammar", "aura", "couple", "monkey", "brain"]:
        app.add_handler(CommandHandler(c, fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    print("/belu loaded! Core Bot with TicTac & Kitty is Live! 🔥")
    app.run_polling()

if __name__ == '__main__': main()
