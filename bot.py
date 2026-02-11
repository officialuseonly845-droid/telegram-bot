import os, logging, random, html, json, asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from flask import Flask, Response
from threading import Thread
import httpx
from groq import Groq

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
PORT = int(os.getenv("PORT", 8080))
BOT_NAME = "miko"  # Bot's name for AI trigger (case-insensitive)
BOT_DISPLAY_NAME = "MIKO"  # Display name in messages
BOT_USERNAME = None  # Will be set on startup

# AI Spam cooldown (user_id -> last_response_time)
ai_cooldown = {}
AI_COOLDOWN_SECONDS = 10

# Initialize Groq
groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        logger.info("✅ Groq AI initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Groq: {e}")

# --- Flask Keep-Alive Server ---
flask_app = Flask(__name__)

@flask_app.route('/')
@flask_app.route('/healthz')
def health():
    return Response("MIKO Bot Online! 🤖", mimetype='text/plain')

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

flask_thread = Thread(target=run_flask, daemon=True)
flask_thread.start()
logger.info(f"🌐 Flask server started on port {PORT}")

# --- Data Persistence ---
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
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump({"daily_locks": daily_locks, "seen_users": seen_users, "tictac_games": tictac_games}, f, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

data_store = load_data()
daily_locks = data_store.get("daily_locks", {})
seen_users = data_store.get("seen_users", {})
tictac_games = data_store.get("tictac_games", {})

# --- AI Functions ---
async def get_ai_response(prompt: str, system_prompt: str = "") -> str:
    """Get AI response using Groq or OpenRouter with fallback"""
    
    # Try Groq first (llama-3.1-8b-instant)
    if GROQ_API_KEY:
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            response = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",  # Updated model
                messages=messages,
                temperature=0.7,
                max_tokens=300  # Shorter responses for Miko
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq error: {e}")
    
    # Fallback to OpenRouter (liquid/lfm-2.5-1.2b-instruct:free)
    if OPENROUTER_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "liquid/lfm-2.5-1.2b-instruct:free",  # Updated model
                        "messages": [
                            {"role": "system", "content": system_prompt} if system_prompt else None,
                            {"role": "user", "content": prompt}
                        ]
                    },
                    timeout=30.0
                )
                result = response.json()
                return result['choices'][0]['message']['content']
        except Exception as e:
            logger.error(f"OpenRouter error: {e}")
    
    # Both failed - return tired message
    return "I'm tired 😴 Try again later!"

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)
    try:
        if update and hasattr(update, 'effective_message') and update.effective_message:
            await update.effective_message.reply_text("⚠️ Error occurred. Try again!")
    except:
        pass

# --- Kitty Command ---
KITTY_PHOTOS = [
    "https://i.postimg.cc/8kKLbdqh/IMG-20260209-195025-385.jpg",
    "https://i.postimg.cc/25cQZKnR/410e358034ac6b1204b7168eb79d8f72.jpg",
    "https://i.postimg.cc/DzjrXMLt/71MShhna21L_UF1000_1000_QL80.jpg",
    "https://i.postimg.cc/FKWyLB3w/girl_with_anime_her_head_777271_50263.jpg",
    "https://i.postimg.cc/W4WGkHgH/images_(2).jpg",
    "https://i.postimg.cc/LsCt1bL7/images_(3).jpg",
    "https://i.postimg.cc/s26YhN5F/images_(4).jpg",
    "https://i.postimg.cc/CKPGqQbp/images_(5).jpg",
    "https://i.postimg.cc/Jh6Nk2ZY/IMG_20260209_110418_808.jpg",
    "https://i.postimg.cc/pTmDYJJ1/kyu_nahi_ho_rahi_padhai_v0_im1xf6f2u6ae1.jpg",
]
chat_kitty_index = {}

async def delete_message_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int):
    """Delete message after specified delay in seconds"""
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Deleted kitty message {message_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"Failed to delete message: {e}")

async def kitty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        chat_kitty_index[chat_id] = 0
        
        keyboard = [[
            InlineKeyboardButton("🌸💗 Next ➡️ 💗🌸", callback_data="kitty_next"),
            InlineKeyboardButton("🍎❤️ Refresh 🔃 ❤️🍎", callback_data="kitty_refresh")
        ]]
        
        sent_message = await update.message.reply_photo(
            photo=KITTY_PHOTOS[0],
            caption="🌸 Cute Kitty! 🐱✨",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Schedule deletion after 30 minutes (1800 seconds)
        asyncio.create_task(delete_message_after_delay(
            context, 
            update.effective_chat.id, 
            sent_message.message_id, 
            1800
        ))
        
        logger.info(f"✅ Kitty sent to chat {chat_id}, will auto-delete in 30 min")
    except Exception as e:
        logger.error(f"Kitty error: {e}", exc_info=True)
        await update.message.reply_text("❌ Kitty failed!")

async def kitty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        chat_id = str(query.message.chat.id)
        
        if query.data == "kitty_next":
            current = chat_kitty_index.get(chat_id, 0)
            current = (current + 1) % len(KITTY_PHOTOS)
            chat_kitty_index[chat_id] = current
            caption = f"🌸 Kitty #{current + 1} 🐱✨"
        else:
            current = chat_kitty_index.get(chat_id, 0)
            caption = "🔃 Refreshed! 🍎✨"
        
        keyboard = [[
            InlineKeyboardButton("🌸💗 Next ➡️ 💗🌸", callback_data="kitty_next"),
            InlineKeyboardButton("🍎❤️ Refresh 🔃 ❤️🍎", callback_data="kitty_refresh")
        ]]
        
        await query.edit_message_media(
            media=InputMediaPhoto(media=KITTY_PHOTOS[current], caption=caption),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Kitty callback error: {e}")

# --- TicTac Game (Redesigned) ---
def create_board():
    return [["🟦" for _ in range(3)] for _ in range(3)]  # Blue empty cells

def get_keyboard(gid, board):
    """Create keyboard with BIG buttons for better visibility"""
    keyboard = []
    for i in range(3):
        row = []
        for j in range(3):
            # Make buttons MUCH wider - large square appearance
            cell = board[i][j]
            if cell == "🟦":
                button_text = "           🟦           "  # Empty cell - large blue square
            elif cell == "🔴":
                button_text = "           ❌           "  # Red X
            else:  # 🟢
                button_text = "           ⭕           "  # Green O
            row.append(InlineKeyboardButton(button_text, callback_data=f"tictac_{gid}_{i}_{j}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def check_winner(board):
    def sym(cell):
        if cell == "🔴": return "X"  # Red = X (❌)
        if cell == "🟢": return "O"  # Green = O (⭕)
        return None
    
    for row in board:
        s = [sym(cell) for cell in row]
        if s[0] and s[0] == s[1] == s[2]: return s[0]
    
    for col in range(3):
        s = [sym(board[r][col]) for r in range(3)]
        if s[0] and s[0] == s[1] == s[2]: return s[0]
    
    s = [sym(board[i][i]) for i in range(3)]
    if s[0] and s[0] == s[1] == s[2]: return s[0]
    
    s = [sym(board[i][2-i]) for i in range(3)]
    if s[0] and s[0] == s[1] == s[2]: return s[0]
    
    if all(c != "🟦" for row in board for c in row): return "Draw"
    return None

def bot_move(board):
    def sym(c):
        if c == "🟢": return "X"
        if c == "🔴": return "O"
        return None
    
    def check_line(pos):
        s = [sym(board[r][c]) for r, c in pos]
        empty = [pos[i] for i, x in enumerate(s) if x is None]
        if s.count("O") == 2 and s.count(None) == 1: return empty[0], "win"
        if s.count("X") == 2 and s.count(None) == 1: return empty[0], "block"
        return None, None
    
    lines = []
    for i in range(3): lines.append([(i, 0), (i, 1), (i, 2)])
    for j in range(3): lines.append([(0, j), (1, j), (2, j)])
    lines.append([(0, 0), (1, 1), (2, 2)])
    lines.append([(0, 2), (1, 1), (2, 0)])
    
    for line in lines:
        p, t = check_line(line)
        if t == "win": return p
    
    for line in lines:
        p, t = check_line(line)
        if t == "block": return p
    
    if board[1][1] == "🟦": return (1, 1)
    
    corners = [(0, 0), (0, 2), (2, 0), (2, 2)]
    empty = [(r, c) for r, c in corners if board[r][c] == "🟦"]
    if empty: return random.choice(empty)
    
    for i in range(3):
        for j in range(3):
            if board[i][j] == "🟦": return (i, j)
    return None

async def tictac_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Check if user has active game (within 10 minutes)
        user_id = update.effective_user.id
        current_time = datetime.now()
        
        # Find if user has any active game
        for gid, game in list(tictac_games.items()):
            if game.get("player_x") == user_id or game.get("player_o") == user_id:
                # Check if game is within 10 minutes
                game_time = datetime.fromisoformat(game.get("created_at", "2000-01-01T00:00:00"))
                if current_time - game_time < timedelta(minutes=10) and not game.get("winner"):
                    await update.message.reply_text("⏳ You already have an active game! Finish it first or wait 10 minutes.")
                    return
        
        gid = f"{update.effective_chat.id}_{update.message.message_id}"
        
        if update.message.reply_to_message and not update.message.reply_to_message.from_user.is_bot:
            opp_id = update.message.reply_to_message.from_user.id
            opp_name = update.message.reply_to_message.from_user.first_name
            vs_bot = False
        else:
            opp_id = None
            opp_name = BOT_DISPLAY_NAME  # Use "MIKO" as opponent name
            vs_bot = True
        
        board = create_board()
        tictac_games[gid] = {
            "board": board,
            "current_turn": "X",  # X = Green with ⭕, O = Red with ❌
            "player_x": update.effective_user.id,
            "player_o": opp_id,
            "player_x_name": update.effective_user.first_name,
            "player_o_name": opp_name,
            "vs_bot": vs_bot,
            "winner": None,
            "created_at": current_time.isoformat()
        }
        save_data()
        
        # New format: "🎮 Tic Tac Toe Started" with player names
        if vs_bot:
            cap = f"🎮 <b>Tic Tac Toe Started</b>\n\n"
            cap += f"<b>{html.escape(update.effective_user.first_name)} ❌</b> vs <b>{BOT_DISPLAY_NAME} ⭕</b>\n\n"
            cap += f"<i>Turn: ❌ {html.escape(update.effective_user.first_name)}</i>"
        else:
            cap = f"🎮 <b>Tic Tac Toe</b>\n\n"
            cap += f"<b>{html.escape(update.effective_user.first_name)} ❌</b> vs <b>{html.escape(opp_name)} ⭕</b>\n\n"
            cap += f"<i>Turn: ❌ {html.escape(update.effective_user.first_name)}</i>"
        
        await update.message.reply_text(cap, reply_markup=get_keyboard(gid, board), parse_mode=ParseMode.HTML)
        logger.info(f"✅ TicTac started: {gid}")
    except Exception as e:
        logger.error(f"TicTac error: {e}", exc_info=True)
        await update.message.reply_text("❌ Game failed!")

async def tictac_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        parts = query.data.split("_")
        if len(parts) < 5: return
        
        gid = f"{parts[1]}_{parts[2]}"
        row, col = int(parts[3]), int(parts[4])
        
        if gid not in tictac_games:
            await query.answer("❌ Game expired!", show_alert=True)
            return
        
        game = tictac_games[gid]
        board = game["board"]
        uid = query.from_user.id
        
        # Check game timeout (5 minutes)
        game_time = datetime.fromisoformat(game.get("created_at", datetime.now().isoformat()))
        if datetime.now() - game_time > timedelta(minutes=5):
            game["winner"] = "Timeout"
            save_data()
            await query.answer("⏰ Game timed out after 5 minutes of inactivity", show_alert=True)
            await query.edit_message_text("⏰ <b>Game Timeout</b>\n\nThis game was inactive for too long.", parse_mode=ParseMode.HTML)
            return
        
        # Update last activity time
        game["created_at"] = datetime.now().isoformat()
        
        # Check if player is part of this game (for 2-player games)
        if not game["vs_bot"]:
            if uid != game["player_x"] and uid != game["player_o"]:
                await query.answer("🔒 You are not part of this game.", show_alert=True)
                return
        
        if game["winner"]:
            await query.answer("Game finished!", show_alert=True)
            return
        
        if board[row][col] != "🟦":
            await query.answer("Cell taken!", show_alert=True)
            return
        
        uid = query.from_user.id
        if game["current_turn"] == "X" and uid != game["player_x"]:
            if not game["vs_bot"]:
                await query.answer("Not your turn!", show_alert=True)
                return
        elif game["current_turn"] == "O" and uid != game["player_o"] and not game["vs_bot"]:
            await query.answer("Not your turn!", show_alert=True)
            return
        
        # X = Player 1 (❌ Red), O = Player 2 (⭕ Green)
        if game["current_turn"] == "X":
            board[row][col] = "🔴"  # Red for X (❌)
            game["current_turn"] = "O"
        else:
            board[row][col] = "🟢"  # Green for O (⭕)
            game["current_turn"] = "X"
        
        winner = check_winner(board)
        
        if winner:
            game["winner"] = winner
            if winner == "Draw":
                cap = f"🎮 <b>Game Over!</b>\n\n"
                cap += f"🤝 <b>It's a Draw!</b> 🤝"
            else:
                # X wins = ❌, O wins = ⭕
                wname = game['player_x_name'] if winner == "X" else game['player_o_name']
                wsym = "❌" if winner == "X" else "⭕"
                cap = f"🎉 <b>GAME OVER!</b> 🎉\n\n"
                cap += f"<b>Winner: {html.escape(wname)} {wsym}</b> 🏆\n\n"
                cap += f"🌟 Congratulations! 🌟"
        else:
            # Show current turn
            csym = "❌" if game["current_turn"] == "X" else "⭕"
            cname = game['player_x_name'] if game["current_turn"] == "X" else game['player_o_name']
            if game["vs_bot"]:
                cap = f"🎮 <b>Tic Tac Toe</b>\n\n"
                cap += f"<b>{html.escape(game['player_x_name'])} ❌</b> vs <b>{BOT_DISPLAY_NAME} ⭕</b>\n\n"
            else:
                cap = f"🎮 <b>Tic Tac Toe</b>\n\n"
                cap += f"<b>{html.escape(game['player_x_name'])} ❌</b> vs <b>{html.escape(game['player_o_name'])} ⭕</b>\n\n"
            cap += f"<i>Turn: {csym} {html.escape(cname)}</i>"
        
        save_data()
        await query.edit_message_text(cap, reply_markup=get_keyboard(gid, board), parse_mode=ParseMode.HTML)
        
        if game["vs_bot"] and not game["winner"] and game["current_turn"] == "O":
            await asyncio.sleep(0.8)
            
            bp = bot_move(board)
            if bp:
                br, bc = bp
                board[br][bc] = "🟢"  # Bot plays as ⭕ (green)
                game["current_turn"] = "X"
                
                winner = check_winner(board)
                if winner:
                    game["winner"] = winner
                    if winner == "Draw":
                        cap = f"🎮 <b>Game Over!</b>\n\n"
                        cap += f"🤝 <b>It's a Draw!</b> 🤝"
                    else:
                        wname = game['player_x_name'] if winner == "X" else game['player_o_name']
                        wsym = "❌" if winner == "X" else "⭕"
                        cap = f"🎉 <b>GAME OVER!</b> 🎉\n\n"
                        cap += f"<b>Winner: {html.escape(wname)} {wsym}</b> 🏆\n\n"
                        cap += f"🌟 Congratulations! 🌟"
                else:
                    cap = f"🎮 <b>Tic Tac Toe</b>\n\n"
                    cap += f"<b>{html.escape(game['player_x_name'])} ❌</b> vs <b>{BOT_DISPLAY_NAME} ⭕</b>\n\n"
                    cap += f"<i>Turn: ❌ {html.escape(game['player_x_name'])}</i>"
                
                save_data()
                await query.edit_message_text(cap, reply_markup=get_keyboard(gid, board), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"TicTac callback error: {e}", exc_info=True)

# --- Fun Commands ---
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

        mapping = {
            "gay": ["🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 <b>{u}</b> Diva: {p}%! ✨", "💄 Gay radar: <b>{u}</b> ({p}%) 🏳️‍🌈", "👠 <b>{u}</b>, slay! {p}% 👑", "🍭 Sweet & Gay: <b>{u}</b> ({p}%)", "🏳️‍🌈 Proudly Gay: <b>{u}</b> ({p}%)", "🦄 Unicorn energy: {p}%!", "✨ <b>{u}</b> is {p}% glittery! 🏳️‍🌈", "👦 <b>{u}</b> loves boys {p}%! 💖", "💄 Hetero dropped: {p}% 📉", "🎀 <b>{u}</b> is {p}% feminine! 💅", "🌈 Rainbow: <b>{u}</b> {p}%! 🍭", "💅 Closet exit: {p}% 🏳️‍🌈", "👗 Skirt looks good! {p}% 💃"],
            "roast": ["💀 <b>{u}</b> is garbage! 🚮", "🗑️ <b>{u}</b>'s face is a crime! 😭", "🤡 <b>{u}</b> has 0 brain cells! 🚫", "🔥 Roasted: <b>{u}</b>! 🍗", "🚑 <b>{u}</b> needs help! 💨", "🧟 Zombies ignored <b>{u}</b>! 🧠", "📉 IQ = room temp: <b>{u}</b>! 🧊", "🚮 Shampoo instructions needed!", "💩 Birth certificate = apology! 👶", "🛑 Stop talking, IQ drops! 📉", "🤮 Looking at <b>{u}</b> hurts! 🚫", "🦴 Uses straw as ladder! 🥢", "🤡 Brain in knees? 🦴", "🚮 Dustbin said no! 🗑️"],
            "chammar": ["🚽 <b>Shakti</b> (Harpic CEO)! 🧴", "🧹 Mop is smarter! 🏆", "🪠 Sultan of Sewage! 🚽", "🧴 Perfume = Harpic Blue! 🧼", "🧼 Scrub harder! {p}% left! 🧼", "🧹 Olympic Mop Winner! 🥇", "🚽 Kingdom = urinal! 🏰", "🧴 Drinks Harpic! 🥛", "🧼 Floor ganda chhoda! 🧹", "🪣 Bucket list: Balti! 🪣"],
            "aura": ["✨ <b>{u}</b>'s Aura: {p}% 👑", "📉 -{p} Aura! 💀", "🌟 Glowing {p}%! 🌌", "🌑 Cardboard: {p}% 📦", "🔥 Godly: {p}%! ⚡", "💩 Shitty: {p}%! 🤢", "🗿 Chad: {p}%! 🗿", "💎 Diamond: {p}%! ✨", "🤡 Clown: {p}%! 🎪", "🌈 Rainbow: {p}%! 🏳️‍🌈"],
            "couple": ["💞 <b>{u1}</b> ❤️ <b>{u2}</b> ({p}%)! 🏩", "💍 Wedding bells! {p}% 🔔", "🔥 Toxic goals! {p}% ☢️", "💕 Rab Ne Bana Di! ({p}%) 🥰", "💔 Breakup loading! {p}% 📉", "🥀 One-sided! ({p}%) 😭", "💑 Perfect pair! {p}% 💖"],
            "monkey": ["🐒 <b>{u}</b> is {p}% Gorilla! 🦍", "🍌 Banana lover! 🐵", "🐒 Jungle king! ({p}%) 🌲", "🦧 Pure Orangutan! 🐵", "🐒 Monkey business! 🍌"],
            "brain": ["🧠 <b>{u}</b>'s Brain: {p}% 🔋", "💡 Intelligence: {p}%! 🕯️", "🥔 Potato Brain: {p}%! 🥔", "⚙️ Processing {p}% slow! 🐌", "🧠 Big Brain: {p}%! ⚡"]
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
        logger.error(f"Fun error: {e}")
        await update.message.reply_text("❌ Command failed!")

# --- Message Handler (with AI name recognition) ---
async def core_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.effective_user or update.effective_user.is_bot: 
            return
        
        chat_id, user_id = str(update.effective_chat.id), str(update.effective_user.id)
        if chat_id not in seen_users: 
            seen_users[chat_id] = {}
        seen_users[chat_id][user_id] = {"n": html.escape(update.effective_user.first_name)}
        save_data()
        
        # Check spam cooldown
        current_time = datetime.now()
        last_response_time = ai_cooldown.get(user_id)
        if last_response_time:
            time_diff = (current_time - last_response_time).total_seconds()
            if time_diff < AI_COOLDOWN_SECONDS:
                return  # Silent cooldown, don't respond
        
        # Check if message mentions bot name "MIKO" or is reply to bot
        should_respond = False
        user_message = ""
        
        message_text = update.message.text.lower() if update.message.text else ""
        
        # Check if replying to bot's message
        if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
            should_respond = True
            user_message = update.message.text.strip() if update.message.text else ""
        # Check if message contains "miko" (case-insensitive)
        elif BOT_NAME in message_text:
            should_respond = True
            # Extract message (everything in the text)
            user_message = update.message.text.strip() if update.message.text else ""
        
        if should_respond and user_message:
            # Update cooldown
            ai_cooldown[user_id] = current_time
            
            # Miko's persona - cute, witty, confident, friendly girl (20-24)
            sys = f"""You are Miko, a 22-year-old friendly girl who loves chatting! 🌸

Personality:
- Cute, witty, confident, friendly
- A little teasing but always respectful
- Warm, playful, sometimes flirty (never explicit)
- Cheerful and positive

Speaking style:
- Short sentences (1-3 sentences max)
- Use emojis occasionally ✨😊💫
- Keep it light and fun
- Never mention being AI or a bot
- You're just Miko, a real girl who loves helping!

Boundaries:
- No sexual or explicit content
- No romantic promises
- If flirting gets too much, gently redirect
- Be encouraging and supportive

Remember: You're Miko, a friendly girl, not a machine! 💫"""
            
            resp = await get_ai_response(user_message, sys)
            
            # Send response directly without "thinking" message
            if resp and resp != "I'm tired 😴 Try again later!":
                await update.message.reply_text(f"{html.escape(resp)}", parse_mode=ParseMode.HTML)
            elif resp:
                # Tired message
                await update.message.reply_text(resp)
    except Exception as e:
        logger.error(f"Message handler error: {e}")

# --- Callback Router ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        if query.data.startswith("kitty_"):
            await kitty_callback(update, context)
        elif query.data.startswith("tictac_"):
            await tictac_callback(update, context)
        else:
            await query.answer("❌ Unknown!")
    except Exception as e:
        logger.error(f"Callback error: {e}")

# --- Main ---
def main():
    logger.info("🚀 Starting MIKO Bot...")
    
    app = Application.builder().token(TOKEN).build()
    app.add_error_handler(error_handler)
    
    app.add_handler(CommandHandler("kitty", kitty_command))
    app.add_handler(CommandHandler("tictac", tictac_command))
    
    for c in ["gay", "roast", "chammar", "aura", "couple", "monkey", "brain"]:
        app.add_handler(CommandHandler(c, fun_dispatcher))
    
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, core_msg_handler))
    
    logger.info("🔥 MIKO Bot Online!")
    logger.info("✨ Commands: /kitty /tictac /gay /roast /chammar /aura /couple /monkey /brain")
    logger.info("💬 AI responds when 'MIKO' is mentioned or when users reply to bot messages")
    logger.info(f"⏱️  AI cooldown: {AI_COOLDOWN_SECONDS} seconds")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__': 
    main()
