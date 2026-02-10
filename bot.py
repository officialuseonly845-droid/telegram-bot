import os, logging, random, html, json, asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from aiohttp.web import Application as WebApp, AppRunner, TCPSite, Response

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Persistence ---
DATA_FILE = "beluga_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: 
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return {"daily_locks": {}, "seen_users": {}, "tictac_games": {}}
    return {"daily_locks": {}, "seen_users": {}, "tictac_games": {}}

def save_data():
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump({
                "daily_locks": daily_locks, 
                "seen_users": seen_users,
                "tictac_games": tictac_games
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

data_store = load_data()
daily_locks = data_store.get("daily_locks", {})
seen_users = data_store.get("seen_users", {})
tictac_games = data_store.get("tictac_games", {})

# --- Config ---
TOKEN = "YOUR_BOT_TOKEN_HERE"
PORT = 8080

# --- HTTP Server for Render (Keep Alive) ---
ADVICE = """
Fuck excuses, keep fucking going, learn from every fuck up, fuck the doubt in your fucking head, and build your fucking life in your own fucking way.
"""

async def checkHealth(request):
    return Response(text=ADVICE, content_type="text/plain")

async def startServer() -> None:
    app = WebApp()
    app.router.add_get('/', checkHealth)
    app.router.add_get('/healthz', checkHealth)
    runner = AppRunner(app, access_log=None)
    
    await runner.setup()
    site = TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 HTTP server listening on port {PORT}")

# --- Global Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify user"""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    # Try to send error message to user
    try:
        if update and hasattr(update, 'effective_message') and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Oops! Something went wrong. Please try again later."
            )
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")

# --- Kitty Photos Gallery ---
KITTY_PHOTOS = [
    "https://i.postimg.cc/8kKLbdqh/IMG-20260209-195025-385.jpg",  # Your custom image
    "https://cataas.com/cat/cute",
    "https://cataas.com/cat/says/Hello",
    "https://placekitten.com/400/300",
    "https://placekitten.com/500/400",
    "https://cataas.com/cat/gif",
]

# Current photo indices for each chat
chat_kitty_index = {}

# --- /kitty Command ---
async def kitty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        chat_kitty_index[chat_id] = 0  # Start from first photo (your custom image)
        
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
        logger.info(f"Kitty command executed by {update.effective_user.id}")
    except Exception as e:
        logger.error(f"Error in kitty_command: {e}")
        await update.message.reply_text("❌ Failed to load kitty photo. Try again!")

async def kitty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        chat_id = str(query.message.chat.id)
        action = query.data
        
        if action == "kitty_next":
            # Move to next photo
            current_index = chat_kitty_index.get(chat_id, 0)
            current_index = (current_index + 1) % len(KITTY_PHOTOS)
            chat_kitty_index[chat_id] = current_index
            caption = f"🌸 Kitty #{current_index + 1} 🐱✨"
        else:  # kitty_refresh
            # Keep current photo but refresh
            current_index = chat_kitty_index.get(chat_id, 0)
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
        except Exception as e:
            # If photo is same, just update caption
            logger.info(f"Media edit failed, updating caption only: {e}")
            await query.edit_message_caption(
                caption=caption,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error in kitty_callback: {e}")

# --- /tictac Command (Colored Tic-Tac-Toe with Moderate AI) ---
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
    # Convert emoji to symbols for checking
    def get_symbol(cell):
        if cell == "🟥": return "X"
        if cell == "🟩": return "O"
        return None
    
    # Check rows
    for row in board:
        symbols = [get_symbol(cell) for cell in row]
        if symbols[0] and symbols[0] == symbols[1] == symbols[2]:
            return symbols[0]
    
    # Check columns
    for col in range(3):
        symbols = [get_symbol(board[row][col]) for row in range(3)]
        if symbols[0] and symbols[0] == symbols[1] == symbols[2]:
            return symbols[0]
    
    # Check diagonals
    symbols = [get_symbol(board[i][i]) for i in range(3)]
    if symbols[0] and symbols[0] == symbols[1] == symbols[2]:
        return symbols[0]
    
    symbols = [get_symbol(board[i][2-i]) for i in range(3)]
    if symbols[0] and symbols[0] == symbols[1] == symbols[2]:
        return symbols[0]
    
    # Check for draw
    if all(cell != "⬜" for row in board for cell in row):
        return "Draw"
    
    return None

def bot_move_moderate(board):
    """Moderate AI - tries to win, block, or make strategic moves"""
    
    def get_symbol(cell):
        if cell == "🟥": return "X"
        if cell == "🟩": return "O"
        return None
    
    def check_line(positions):
        """Check if bot can win or needs to block on this line"""
        symbols = [get_symbol(board[r][c]) for r, c in positions]
        empty_pos = [positions[i] for i, s in enumerate(symbols) if s is None]
        
        if symbols.count("O") == 2 and symbols.count(None) == 1:
            return empty_pos[0], "win"  # Bot can win
        if symbols.count("X") == 2 and symbols.count(None) == 1:
            return empty_pos[0], "block"  # Must block player
        return None, None
    
    # All possible lines
    lines = []
    # Rows
    for i in range(3):
        lines.append([(i, 0), (i, 1), (i, 2)])
    # Columns
    for j in range(3):
        lines.append([(0, j), (1, j), (2, j)])
    # Diagonals
    lines.append([(0, 0), (1, 1), (2, 2)])
    lines.append([(0, 2), (1, 1), (2, 0)])
    
    # 1. Try to win
    for line in lines:
        pos, move_type = check_line(line)
        if move_type == "win":
            return pos
    
    # 2. Block opponent
    for line in lines:
        pos, move_type = check_line(line)
        if move_type == "block":
            return pos
    
    # 3. Take center if available
    if board[1][1] == "⬜":
        return (1, 1)
    
    # 4. Take a corner
    corners = [(0, 0), (0, 2), (2, 0), (2, 2)]
    empty_corners = [(r, c) for r, c in corners if board[r][c] == "⬜"]
    if empty_corners:
        return random.choice(empty_corners)
    
    # 5. Take any edge
    edges = [(0, 1), (1, 0), (1, 2), (2, 1)]
    empty_edges = [(r, c) for r, c in edges if board[r][c] == "⬜"]
    if empty_edges:
        return random.choice(empty_edges)
    
    # 6. Take any empty cell
    for i in range(3):
        for j in range(3):
            if board[i][j] == "⬜":
                return (i, j)
    
    return None

async def tictac_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
        logger.info(f"TicTac game started: {game_id}")
    except Exception as e:
        logger.error(f"Error in tictac_command: {e}")
        await update.message.reply_text("❌ Failed to start game. Try again!")

async def tictac_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data_parts = query.data.split("_")
        if len(data_parts) < 5:
            await query.answer("❌ Invalid game data!", show_alert=True)
            return
            
        game_id = f"{data_parts[1]}_{data_parts[2]}"
        row, col = int(data_parts[3]), int(data_parts[4])
        
        if game_id not in tictac_games:
            await query.answer("❌ Game expired or not found!", show_alert=True)
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
            await asyncio.sleep(0.8)  # Delay for realism
            
            bot_pos = bot_move_moderate(board)
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
    except Exception as e:
        logger.error(f"Error in tictac_callback: {e}")

# --- Fun Commands Dispatcher ---
async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Error in fun_dispatcher: {e}")
        await update.message.reply_text("❌ Command failed. Try again!")

# --- Tracking ---
async def core_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.effective_user or update.effective_user.is_bot: 
            return
        chat_id, user_id = str(update.effective_chat.id), str(update.effective_user.id)
        if chat_id not in seen_users: 
            seen_users[chat_id] = {}
        seen_users[chat_id][user_id] = {"n": html.escape(update.effective_user.first_name)}
        save_data()
    except Exception as e:
        logger.error(f"Error in core_msg_handler: {e}")

# --- Main Callback Query Router ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route callback queries to appropriate handlers"""
    try:
        query = update.callback_query
        
        if query.data.startswith("kitty_"):
            await kitty_callback(update, context)
        elif query.data.startswith("tictac_"):
            await tictac_callback(update, context)
        else:
            await query.answer("❌ Unknown action!")
    except Exception as e:
        logger.error(f"Error in callback_query_handler: {e}")

# --- Main ---
async def main() -> None:
    """Main function to start bot and server"""
    # Start HTTP server for Render keep-alive
    await startServer()
    
    # Build bot application
    app = Application.builder().token(TOKEN).build()
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Add command handlers
    app.add_handler(CommandHandler("kitty", kitty_command))
    app.add_handler(CommandHandler("tictac", tictac_command))
    
    for c in ["gay", "roast", "chammar", "aura", "couple", "monkey", "brain"]:
        app.add_handler(CommandHandler(c, fun_dispatcher))
    
    # Add callback query handler
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # Add message handler for tracking users
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    
    logger.info("🔥 Beluga Bot is Online! 🔥")
    logger.info("✨ Commands: /kitty, /tictac, /gay, /roast, /chammar, /aura, /couple, /monkey, /brain")
    
    # Start polling
    await app.run_polling()

if __name__ == '__main__': 
    asyncio.run(main())
