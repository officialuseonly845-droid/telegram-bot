import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app for uptime monitoring
app = Flask(__name__)

# Daily locks storage (chat_id -> {command: {date, user, data}})
daily_locks = {}

# Roast messages
ROASTS = [
    "💀 {user} got roasted harder than a marshmallow! 🔥",
    "😂 {user} needs ice for that BURN! 🧊",
    "🤡 {user} just became the group's comedy show!",
    "💥 {user} got destroyed! Call 911! 🚑",
    "🔥 {user} is now extra crispy! 😭"
]

SIMP_MESSAGES = [
    "💘 {user} is today's official SIMP! 🌚",
    "😍 {user} simping level: MAXIMUM! 📈",
    "💞 {user} won the simp championship! 🏆",
    "🤡 {user} = Professional Simp 💯"
]

LEGEND_MESSAGES = [
    "👑 {user} is THE LEGEND today! 😎",
    "⚡ {user} unlocked LEGEND status! 🔥",
    "🌟 All hail {user}, today's LEGEND! 👑",
    "💪 {user} is the group ICON! 🔥"
]

NOOB_MESSAGES = [
    "🍼 {user} is today's official NOOB! 😂",
    "🤣 {user} needs a tutorial! 📖",
    "😅 {user} = Beginner mode activated! 🎮",
    "🤡 {user} still learning the basics! 📚"
]

BRAIN_MESSAGES = [
    "🧠 {user}'s brain power: {pct}% 🤯",
    "💭 {user}'s IQ today: {pct}% 😂",
    "🤓 {user} is {pct}% smart today! 📊",
    "🧠 {user}'s brain cells active: {pct}% 🔬"
]

SLEEP_MESSAGES = [
    "😴 {user} is the sleepyhead of the day! 💤",
    "🛌 {user} needs coffee ASAP! ☕",
    "💤 {user} = Professional Sleeper 😂",
    "😪 {user} forgot to wake up! ⏰"
]

FOODIE_MESSAGES = [
    "🍔 {user} is today's FOODIE! 🍕",
    "😋 {user} lives to EAT! 🍰",
    "🍟 {user} = Food Champion! 🏆",
    "🍜 {user} is always HUNGRY! 😂"
]

DEAD_MESSAGES = [
    "💀 {user} is DONE for today! 😂",
    "⚰️ {user} has left the chat (mentally) 💀",
    "😵 {user} is officially DEAD! 🪦",
    "💀 RIP {user}, you tried! 🕊️"
]

MONKEY_MESSAGES = [
    "🐒 {user} is the group MONKEY! 🙈",
    "🍌 {user} = Official Banana Lover! 🐵",
    "🙉 {user} is going APE! 🐒",
    "🐵 {user} needs a zoo! 😂"
]

CAP_MESSAGES = [
    "🧢 {user} is CAPPING hard! 🤥",
    "🤡 {user} lying level: 100% 🧢",
    "😂 {user} = Professional Liar! 🧢",
    "🧢 {user} stop the CAP! 🛑"
]

SUS_MESSAGES = [
    "🤔 {user} is SUS today! 🚨",
    "🕵️ {user} acting SUSPICIOUS! 👀",
    "🚨 {user} = Imposter vibes! 🤡",
    "😬 {user} is doing something shady! 🕵️"
]

DANCE_MESSAGES = [
    "🕺 {user} is DANCING! 💃",
    "💃 {user} got the moves! 🔥",
    "🎵 {user} = Dance Champion! 🕺",
    "🪩 {user} is on fire! 💃"
]

MIRROR_MESSAGES = [
    "🪞 {user} looked in the mirror and ran away! 😬",
    "😂 {user}'s reflection called for help! 🪞",
    "🤡 {user}'s mirror cracked! 💀",
    "🪞 {user}'s reflection needs therapy! 😭"
]

RANDOM_MESSAGES = [
    "🎲 {user} got a {pct}% random rating! 🤡",
    "🎰 {user}'s vibe today: {pct}% 😂",
    "🎲 Random stats for {user}: {pct}% 🔥",
    "🤪 {user} = {pct}% chaos energy! 🎲"
]

CHAMMAR_ROASTS = [
    "SHAKTI? More like TOILET KING 🤡🚽, cleaning more than just his ego!",
    "Watch out, everyone… Shakti is coming with his mop of doom! 🧹🤡",
    "SHAKTI 💪🔥? Nah, more like SHAKTI 🤡🪣, master of flushing dreams!",
    "Here comes Shakti, proving every day that scrubbing toilets > scrubbing reputations 🤡",
    "Legends say Shakti once tried to touch fame… got flushed immediately 🚽🤣",
    "SHAKTI's special skill: making toilets shine and hopes die simultaneously 🤣🤡",
    "If humility was a toilet, Shakti would own the throne 🤡🚽"
]

# Flask routes
@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "bot": "Telegram Fun Bot",
        "uptime": "active"
    })

@app.route('/ping')
def ping():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "service": "telegram-bot"})

def run_flask():
    """Run Flask server in a separate thread"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

async def get_random_member(update: Update):
    """Get a random human member from the chat"""
    try:
        chat_id = update.effective_chat.id
        admins = await update.effective_chat.get_administrators()
        
        # Filter out bots
        human_members = [admin.user for admin in admins if not admin.user.is_bot]
        
        if human_members:
            return random.choice(human_members)
        
        # Fallback to message sender
        return update.effective_user
    except Exception as e:
        logger.error(f"Error getting random member: {e}")
        return update.effective_user

def get_user_display(user):
    """Get user display name with @ if available"""
    if user.username:
        return f"@{user.username}"
    return user.first_name

def check_daily_lock(chat_id, command):
    """Check if command is locked for today, return None if expired or doesn't exist"""
    today = datetime.now().date()
    
    if chat_id not in daily_locks:
        return None
    
    if command in daily_locks[chat_id]:
        lock_date = daily_locks[chat_id][command]['date']
        if lock_date == today:
            return daily_locks[chat_id][command]
    
    return None

def set_daily_lock(chat_id, command, data):
    """Set daily lock for a command with data"""
    today = datetime.now().date()
    
    if chat_id not in daily_locks:
        daily_locks[chat_id] = {}
    
    daily_locks[chat_id][command] = {
        'date': today,
        'data': data
    }

# Command handlers
async def gay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pick today's gay"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'gay')
    if locked:
        user = locked['data']['user']
        pct = locked['data']['pct']
        user_display = get_user_display(user)
        await update.message.reply_text(f"🌈 Today's gay is still {user_display}! ({pct}% gay) 🌚")
        return
    
    user = await get_random_member(update)
    pct = random.randint(1, 100)
    set_daily_lock(chat_id, 'gay', {'user': user, 'pct': pct})
    
    user_display = get_user_display(user)
    await update.message.reply_text(f"🌈 Today's gay is {user_display}! ({pct}% gay) 🌚")

async def couple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ship two random members"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'couple')
    if locked:
        user1 = locked['data']['user1']
        user2 = locked['data']['user2']
        pct = locked['data']['pct']
        user1_display = get_user_display(user1)
        user2_display = get_user_display(user2)
        await update.message.reply_text(f"💞 Today's couple is still {user1_display} ❤️ {user2_display}! ({pct}% match)")
        return
    
    user1 = await get_random_member(update)
    user2 = await get_random_member(update)
    
    # Ensure different users
    attempts = 0
    while user1.id == user2.id and attempts < 5:
        user2 = await get_random_member(update)
        attempts += 1
    
    pct = random.randint(1, 100)
    set_daily_lock(chat_id, 'couple', {'user1': user1, 'user2': user2, 'pct': pct})
    
    user1_display = get_user_display(user1)
    user2_display = get_user_display(user2)
    
    await update.message.reply_text(f"💞 Today's couple: {user1_display} ❤️ {user2_display}! ({pct}% match)")

async def cringe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Find the cringe king/queen"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'cringe')
    if locked:
        user = locked['data']['user']
        user_display = get_user_display(user)
        await update.message.reply_text(f"🤡 The cringe king/queen is still {user_display}! 😂")
        return
    
    user = await get_random_member(update)
    set_daily_lock(chat_id, 'cringe', {'user': user})
    
    user_display = get_user_display(user)
    await update.message.reply_text(f"🤡 Today's cringe king/queen is {user_display}! 😂")

async def chammar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Always roasts Shakti - NO DAILY LOCK"""
    roast = random.choice(CHAMMAR_ROASTS)
    await update.message.reply_text(f"💪 {roast}")

async def roast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Roast a random member"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'roast')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(ROASTS).format(user=user_display)
    set_daily_lock(chat_id, 'roast', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def simp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Expose the simp"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'simp')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(SIMP_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'simp', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def legend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Declare today's legend"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'legend')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(LEGEND_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'legend', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def noob(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Call out the noob"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'noob')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(NOOB_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'noob', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def luck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rate someone's luck"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'luck')
    if locked:
        user = locked['data']['user']
        pct = locked['data']['pct']
        user_display = get_user_display(user)
        await update.message.reply_text(f"🍀 {user_display}'s luck today: {pct}% 🎲")
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    pct = random.randint(0, 100)
    set_daily_lock(chat_id, 'luck', {'user': user, 'pct': pct})
    await update.message.reply_text(f"🍀 {user_display}'s luck today: {pct}% 🎲")

async def dance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show someone dancing"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'dance')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(DANCE_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'dance', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def brain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rate brainpower"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'brain')
    if locked:
        user = locked['data']['user']
        pct = locked['data']['pct']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    pct = random.randint(0, 200)
    message = random.choice(BRAIN_MESSAGES).format(user=user_display, pct=pct)
    set_daily_lock(chat_id, 'brain', {'user': user, 'pct': pct, 'message': message})
    await update.message.reply_text(message)

async def sleep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark the sleepyhead"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'sleep')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(SLEEP_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'sleep', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def foodie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pick the foodie"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'foodie')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(FOODIE_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'foodie', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def dead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Declare someone dead"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'dead')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(DEAD_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'dead', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def monkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tag the group monkey"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'monkey')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(MONKEY_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'monkey', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Call out someone lying"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'cap')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(CAP_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'cap', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def sus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark someone suspicious"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'sus')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(SUS_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'sus', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def random_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Give a random rating"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'random')
    if locked:
        user = locked['data']['user']
        pct = locked['data']['pct']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    pct = random.randint(0, 100)
    message = random.choice(RANDOM_MESSAGES).format(user=user_display, pct=pct)
    set_daily_lock(chat_id, 'random', {'user': user, 'pct': pct, 'message': message})
    await update.message.reply_text(message)

async def mirror(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Roast someone's reflection"""
    chat_id = update.effective_chat.id
    
    locked = check_daily_lock(chat_id, 'mirror')
    if locked:
        user = locked['data']['user']
        message = locked['data']['message']
        await update.message.reply_text(message)
        return
    
    user = await get_random_member(update)
    user_display = get_user_display(user)
    message = random.choice(MIRROR_MESSAGES).format(user=user_display)
    set_daily_lock(chat_id, 'mirror', {'user': user, 'message': message})
    await update.message.reply_text(message)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    welcome = """
🎉 Welcome to Fun Bot! 🎉

Available commands:
🌈 /gay – Today's random gay
💞 /couple – Random couple shipping
🤡 /cringe – Cringe king/queen
💪 /chammar – Roast Shakti
💀 /roast – Savage roast
💘 /simp – Simp of the day
👑 /legend – Today's legend
🍼 /noob – Noob of the day
🍀 /luck – Luck rating
🕺 /dance – Dancing member
🧠 /brain – Brain power
😴 /sleep – Sleepyhead
🍔 /foodie – Group foodie
💀 /dead – Done for today
🐒 /monkey – Group monkey
🧢 /cap – Liar callout
🤔 /sus – Suspicious member
🎲 /random – Random rating
🪞 /mirror – Reflection roast

Note: Results stay same for 24 hours (except /chammar)!
"""
    await update.message.reply_text(welcome)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Exception: {context.error}")

def main():
    """Main function"""
    # Get token from environment
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found!")
        return
    
    # Start Flask in background
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started")
    
    # Create application
    application = Application.builder().token(token).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("gay", gay))
    application.add_handler(CommandHandler("couple", couple))
    application.add_handler(CommandHandler("cringe", cringe))
    application.add_handler(CommandHandler("chammar", chammar))
    application.add_handler(CommandHandler("roast", roast))
    application.add_handler(CommandHandler("simp", simp))
    application.add_handler(CommandHandler("legend", legend))
    application.add_handler(CommandHandler("noob", noob))
    application.add_handler(CommandHandler("luck", luck))
    application.add_handler(CommandHandler("dance", dance))
    application.add_handler(CommandHandler("brain", brain))
    application.add_handler(CommandHandler("sleep", sleep))
    application.add_handler(CommandHandler("foodie", foodie))
    application.add_handler(CommandHandler("dead", dead))
    application.add_handler(CommandHandler("monkey", monkey))
    application.add_handler(CommandHandler("cap", cap))
    application.add_handler(CommandHandler("sus", sus))
    application.add_handler(CommandHandler("random", random_cmd))
    application.add_handler(CommandHandler("mirror", mirror))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    logger.info("Bot started polling...")
    
    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
