import os, logging, random, html, json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode

# --- Persistence ---
DATA_FILE = "beluga_data.json"
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: 
                return json.load(f)
        except: 
            return {"daily_locks": {}, "seen_users": {}, "tictac_games": {}}
    return {"daily_locks": {}, "seen_users": {}, "tictac_games": {}}

def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump({
            "daily_locks": daily_locks, 
            "seen_users": seen_users,
            "tictac_games": tictac_games
        }, f)

data_store = load_data()
daily_locks = data_store.get("daily_locks", {})
seen_users = data_store.get("seen_users", {})
tictac_games = data_store.get("tictac_games", {})

# --- Config ---
TOKEN = "YOUR_BOT_TOKEN_HERE"

# --- Kitty Photos Gallery ---
KITTY_PHOTOS = [
    "https://cataas.com/cat/cute",
    "https://cataas.com/cat/says/Hello",
    "https://cataas.com/cat/says/Meow",
    "https://placekitten.com/400/300",
    "https://placekitten.com/500/400",
    "https://cataas.com/cat/gif",
]

# Current photo indices for each user
user_kitty_index = {}

# --- /kitty Command ---
async def kitty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_kitty_index[user_id] = 0  # Start from first photo
    
    photo_url = KITTY_PHOTOS[0]
    caption = "🌸 Cute Kitty! 🐱✨"
    
    keyboard = [
        [
            InlineKeyboardButton("Next ➡️ 🌸", callback_data="kitty_next"),
            InlineKeyboardButton("Refresh 🔃 🍎", callback_data="kitty_refresh")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_photo(
        photo=photo_url,
        caption=caption,
        reply_markup=reply_markup
    )

async def kitty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    action = query.data
    
    if action == "kitty_next":
        # Move to next photo
        current_index = user_kitty_index.get(user_id, 0)
        current_index = (current_index + 1) % len(KITTY_PHOTOS)
        user_kitty_index[user_id] = current_index
        caption = f"🌸 Kitty #{current_index + 1} 🐱✨"
    else:  # kitty_refresh
        # Refresh current photo
        current_index = user_kitty_index.get(user_id, 0)
        caption = "🔃 Refreshed! 🍎 Cute Kitty! 🐱✨"
    
    photo_url = KITTY_PHOTOS[current_index]
    
    keyboard = [
        [
            InlineKeyboardButton("Next ➡️ 🌸", callback_data="kitty_next"),
            InlineKeyboardButton("Refresh 🔃 🍎", callback_data="kitty_refresh")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Edit the message with new photo
    try:
        await query.edit_message_media(
            media={"type": "photo", "media": photo_url},
            reply_markup=reply_markup
        )
        await query.edit_message_caption(
            caption=caption,
            reply_markup=reply_markup
        )
    except:
        # If photo URL is same, just update caption
        await query.edit_message_caption(
            caption=caption,
            reply_markup=reply_markup
        )

# --- /tictac Command (Colored Tic-Tac-Toe) ---
def create_tictac_board():
    """Create empty 3x3 board"""
    return [["⬜" for _ in range(3)] for _ in range(3)]

def get_tictac_keyboard(game_id, board):
    """Generate inline keyboard from board state"""
    keyboard = []
    for i in range(3):
        row = []
        for j in range(3):
            row.append(InlineKeyboardButton(
                board[i][j], 
                callback_data=f"tictac_{game_id}_{i}_{j}"
            ))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def check_winner(board):
    """Check if there's a winner. Returns 'X', 'O', 'Draw', or None"""
    # Check rows
    for row in board:
        if row[0] == row[1] == row[2] and row[0] in ["🟥", "🟩"]:
            return "X" if row[0] == "🟥" else "O"
    
    # Check columns
    for col in range(3):
        if board[0][col] == board[1][col] == board[2][col] and board[0][col] in ["🟥", "🟩"]:
            return "X" if board[0][col] == "🟥" else "O"
    
    # Check diagonals
    if board[0][0] == board[1][1] == board[2][2] and board[0][0] in ["🟥", "🟩"]:
        return "X" if board[0][0] == "🟥" else "O"
    if board[0][2] == board[1][1] == board[2][0] and board[0][2] in ["🟥", "🟩"]:
        return "X" if board[0][2] == "🟥" else "O"
    
    # Check for draw
    if all(cell != "⬜" for row in board for cell in row):
        return "Draw"
    
    return None

def bot_move(board):
    """Simple bot AI - finds first empty cell"""
    for i in range(3):
        for j in range(3):
            if board[i][j] == "⬜":
                return (i, j)
    return None

async def tictac_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = f"{update.effective_chat.id}_{update.message.message_id}"
    
    # Check if replying to someone
    if update.message.reply_to_message and not update.message.reply_to_message.from_user.is_bot:
        opponent_id = update.message.reply_to_message.from_user.id
        opponent_name = update.message.reply_to_message.from_user.first_name
        vs_bot = False
    else:
        opponent_id = None
        opponent_name = "🤖 Bot"
        vs_bot = True
    
    board = create_tictac_board()
    
    tictac_games[game_id] = {
        "board": board,
        "current_turn": "X",
        "player_x": update.effective_user.id,
        "player_o": opponent_id,
        "player_x_name": update.effective_user.first_name,
        "player_o_name": opponent_name,
        "vs_bot": vs_bot,
        "winner": None
    }
    save_data()
    
    caption = f"🎮 <b>Tic-Tac-Toe Game!</b> 🎮\n\n"
    caption += f"🟥 <b>{html.escape(update.effective_user.first_name)}</b> (X)\n"
    caption += f"🟩 <b>{html.escape(opponent_name)}</b> (O)\n\n"
    caption += f"Current Turn: 🟥 <b>X</b>"
    
    keyboard = get_tictac_keyboard(game_id, board)
    
    await update.message.reply_text(
        caption,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

async def tictac_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data_parts = query.data.split("_")
    game_id = f"{data_parts[1]}_{data_parts[2]}"
    row, col = int(data_parts[3]), int(data_parts[4])
    
    if game_id not in tictac_games:
        await query.answer("❌ Game expired!", show_alert=True)
        return
    
    game = tictac_games[game_id]
    board = game["board"]
    
    # Check if game already over
    if game["winner"]:
        await query.answer("🎮 Game is already finished!", show_alert=True)
        return
    
    # Check if cell is empty
    if board[row][col] != "⬜":
        await query.answer("❌ Cell already taken!", show_alert=True)
        return
    
    # Check if it's the player's turn
    current_player = query.from_user.id
    if game["current_turn"] == "X" and current_player != game["player_x"]:
        if not game["vs_bot"]:
            await query.answer("⏳ Not your turn!", show_alert=True)
            return
    elif game["current_turn"] == "O" and current_player != game["player_o"] and not game["vs_bot"]:
        await query.answer("⏳ Not your turn!", show_alert=True)
        return
    
    # Make the move
    if game["current_turn"] == "X":
        board[row][col] = "🟥"  # Red for X
        game["current_turn"] = "O"
    else:
        board[row][col] = "🟩"  # Green for O
        game["current_turn"] = "X"
    
    # Check for winner
    winner = check_winner(board)
    
    if winner:
        game["winner"] = winner
        if winner == "Draw":
            caption = f"🎮 <b>Game Over!</b> 🎮\n\n"
            caption += f"🟥 <b>{html.escape(game['player_x_name'])}</b> (X)\n"
            caption += f"🟩 <b>{html.escape(game['player_o_name'])}</b> (O)\n\n"
            caption += f"🤝 <b>It's a Draw!</b> 🤝"
        else:
            winner_name = game['player_x_name'] if winner == "X" else game['player_o_name']
            winner_emoji = "🟥" if winner == "X" else "🟩"
            
            caption = f"🎉🎊 <b>CONGRATULATIONS!</b> 🎊🎉\n\n"
            caption += f"{winner_emoji} <b>{html.escape(winner_name)}</b> ({winner}) WINS! 🏆✨\n\n"
            caption += f"🌟 Absolute Champion! 🌟\n"
            caption += f"💫 Victory Achieved! 💫"
    else:
        # Game continues
        current_symbol = "🟥 X" if game["current_turn"] == "X" else "🟩 O"
        current_name = game['player_x_name'] if game["current_turn"] == "X" else game['player_o_name']
        
        caption = f"🎮 <b>Tic-Tac-Toe Game!</b> 🎮\n\n"
        caption += f"🟥 <b>{html.escape(game['player_x_name'])}</b> (X)\n"
        caption += f"🟩 <b>{html.escape(game['player_o_name'])}</b> (O)\n\n"
        caption += f"Current Turn: {current_symbol} <b>{html.escape(current_name)}</b>"
    
    save_data()
    keyboard = get_tictac_keyboard(game_id, board)
    
    await query.edit_message_text(
        caption,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    
    # If vs bot and it's bot's turn, make bot move
    if game["vs_bot"] and not game["winner"] and game["current_turn"] == "O":
        import asyncio
        await asyncio.sleep(0.5)  # Small delay for realism
        
        bot_pos = bot_move(board)
        if bot_pos:
            bot_row, bot_col = bot_pos
            board[bot_row][bot_col] = "🟩"
            game["current_turn"] = "X"
            
            # Check winner again
            winner = check_winner(board)
            if winner:
                game["winner"] = winner
                if winner == "Draw":
                    caption = f"🎮 <b>Game Over!</b> 🎮\n\n"
                    caption += f"🟥 <b>{html.escape(game['player_x_name'])}</b> (X)\n"
                    caption += f"🟩 <b>{html.escape(game['player_o_name'])}</b> (O)\n\n"
                    caption += f"🤝 <b>It's a Draw!</b> 🤝"
                else:
                    winner_name = game['player_x_name'] if winner == "X" else game['player_o_name']
                    winner_emoji = "🟥" if winner == "X" else "🟩"
                    
                    caption = f"🎉🎊 <b>CONGRATULATIONS!</b> 🎊🎉\n\n"
                    caption += f"{winner_emoji} <b>{html.escape(winner_name)}</b> ({winner}) WINS! 🏆✨\n\n"
                    caption += f"🌟 Absolute Champion! 🌟\n"
                    caption += f"💫 Victory Achieved! 💫"
            else:
                caption = f"🎮 <b>Tic-Tac-Toe Game!</b> 🎮\n\n"
                caption += f"🟥 <b>{html.escape(game['player_x_name'])}</b> (X)\n"
                caption += f"🟩 <b>{html.escape(game['player_o_name'])}</b> (O)\n\n"
                caption += f"Current Turn: 🟥 <b>X {html.escape(game['player_x_name'])}</b>"
            
            save_data()
            keyboard = get_tictac_keyboard(game_id, board)
            
            await query.edit_message_text(
                caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )

# --- Fun Commands Dispatcher ---
async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    chat_id = str(update.effective_chat.id)
    today = str(datetime.now().date())

    if chat_id not in daily_locks or daily_locks[chat_id].get("date") != today:
        daily_locks[chat_id] = {"date": today, "commands": {}}

    if cmd in daily_locks[chat_id]["commands"]:
        return await update.message.reply_text(f"📌 {daily_locks[chat_id]['commands'][cmd]}", parse_mode=ParseMode.HTML)

    users = list(seen_users.get(chat_id, {}).values())
    if not users:
        return await update.message.reply_text("List khali hai! Thodi chat karo pehle. 🤡")

    # 🔥 PURE 78+ REPLIES - KOI BHI MISS NAHI HAI 🔥
    mapping = {
        "gay": [
            "🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 <b>{u}</b> Diva meter: {p}%! ✨", "💄 Gay radar: <b>{u}</b> ({p}%) 🏳️‍🌈", 
            "👠 <b>{u}</b>, slay queen! {p}% 👑", "🍭 Sweet & Gay: <b>{u}</b> ({p}%)", "🏳️‍🌈 Proudly Gay: <b>{u}</b> ({p}%)",
            "🦄 Unicorn energy: {p}% for <b>{u}</b>!", "✨ <b>{u}</b> is {p}% glittery! 🏳️‍🌈", "👦 <b>{u}</b> loves boys {p}%! 💖",
            "💄 Heterosexuality dropped: {p}% 📉", "🎀 <b>{u}</b> is {p}% feminine tonight! 💅", "🌈 Rainbow boy <b>{u}</b>: {p}%! 🍭",
            "💅 {u} just came out of the closet! {p}% 🏳️‍🌈", "👗 {u} looks great in a skirt! {p}% 💃"
        ],
        "roast": [
            "💀 <b>{u}</b> is pure garbage! 🚮", "🗑️ <b>{u}</b>'s face is a crime scene! 😭", "🤡 <b>{u}</b> has 0 brain cells! 🚫",
            "🔥 Roasted like a chicken: <b>{u}</b>! 🍗", "🚑 <b>{u}</b> needs mental help ASAP! 💨", "🧟 Zombies ignored <b>{u}</b>... no brains! 🧠",
            "📉 IQ lower than room temperature: <b>{u}</b>! 🧊", "🚮 {u} is why shampoo has instructions! 🧴",
            "💩 {u}'s birth certificate is an apology! 👶", "🛑 Stop talking <b>{u}</b>, IQ drop ho raha hai! 📉",
            "🤮 Looking at <b>{u}</b> makes me sad! 🚫", "🦴 {u} is so skinny, he uses a straw as a ladder! 🥢",
            "🤡 {u}, tera dimaag ghutno mein hai kya? 🦴", "🚮 Dustbin bhi <b>{u}</b> ko lene se mana kar raha hai! 🗑️"
        ],
        "chammar": [
            "🚽 <b>Shakti</b> (Harpic CEO) spotted! 🧴", "🧹 <b>Shakti</b>'s mop is smarter! 🏆", "🪠 Sultan of Sewage: <b>Shakti</b>! 🚽",
            "🧴 Perfume? Harpic Blue for <b>Shakti</b>! 🧼", "🧼 Scrub harder <b>Shakti</b>! {p}% left! 🧼", "🧹 Olympic Mop Winner: <b>Shakti</b>! 🥇",
            "🚽 <b>Shakti</b>'s kingdom is the public urinal! 🏰", "🧴 Shakti drinks Harpic for breakfast! 🥛",
            "🧼 Shakti, tune aaj bhi floor ganda chhoda! 🧹", "🪣 Shakti ka bucket list: Sirf ek Balti! 🪣"
        ],
        "aura": [
            "✨ <b>{u}</b>'s Aura: {p}% 👑", "📉 -{p} Aura for <b>{u}</b>! 💀", "🌟 Glowing at {p}%! 🌌", "🌑 Cardboard Aura: {p}% 📦",
            "🔥 Godly Aura: <b>{u}</b> ({p}%)! ⚡", "💩 Shitty Aura: <b>{u}</b> ({p}%)! 🤢", "🗿 Chad Aura: <b>{u}</b> ({p}%)! 🗿",
            "💎 Diamond Aura: <b>{u}</b> ({p}%)! ✨", "🤡 Clown Aura: <b>{u}</b> ({p}%)! 🎪", "🌈 Rainbow Aura: <b>{u}</b> ({p}%)! 🏳️‍🌈"
        ],
        "couple": [
            "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! {p}% 🔔",
            "🔥 Toxic goals: <b>{u1}</b> & <b>{u2}</b>! {p}% ☢️", "💕 Rab Ne Bana Di Jodi: <b>{u1}</b> & <b>{u2}</b>! ({p}%) 🥰",
            "💔 Breakup loading for <b>{u1}</b> & <b>{u2}</b>! {p}% 📉", "🥀 One-sided love: <b>{u1}</b> for <b>{u2}</b>! ({p}%) 😭",
            "💑 Perfect pair: <b>{u1}</b> and <b>{u2}</b>! {p}% 💖"
        ],
        "monkey": [
            "🐒 <b>{u}</b> is {p}% Gorilla! 🦍", "🍌 Banana lover: <b>{u}</b>! 🐵", "🐒 Jungle king: <b>{u}</b>! ({p}%) 🌲",
            "🦧 <b>{u}</b> is a pure Orangutan! 🐵", "🐒 Monkey business detected: <b>{u}</b>! 🍌"
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
        m = random.choice(users)
        res = random.choice(mapping[cmd]).format(u=m['n'], p=random.randint(0, 100))
    
    daily_locks[chat_id]["commands"][cmd] = res
    save_data()
    await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

# --- Tracking ---
async def core_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.is_bot: 
        return
    chat_id, user_id = str(update.effective_chat.id), str(update.effective_user.id)
    if chat_id not in seen_users: 
        seen_users[chat_id] = {}
    seen_users[chat_id][user_id] = {"n": html.escape(update.effective_user.first_name)}
    save_data()

# --- Main Callback Query Router ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route callback queries to appropriate handlers"""
    query = update.callback_query
    
    if query.data.startswith("kitty_"):
        await kitty_callback(update, context)
    elif query.data.startswith("tictac_"):
        await tictac_callback(update, context)
    else:
        await query.answer("❌ Unknown action!")

# --- Main ---
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("kitty", kitty_command))
    app.add_handler(CommandHandler("tictac", tictac_command))
    
    for c in ["gay", "roast", "chammar", "aura", "couple", "monkey", "brain"]:
        app.add_handler(CommandHandler(c, fun_dispatcher))
    
    # Add callback query handler
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # Add message handler for tracking users
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    
    print("🔥 Beluga Bot is Online! 🔥")
    print("✨ Commands loaded: /kitty, /tictac, /gay, /roast, /chammar, /aura, /couple, /monkey, /brain")
    app.run_polling()

if __name__ == '__main__': 
    main()
