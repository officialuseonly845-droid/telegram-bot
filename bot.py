import os
import logging
import random
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app for health checks (keeps Render instance alive)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running! ✅", 200

@app.route('/health')
def health():
    return "OK", 200

@app.route('/ping')
def ping():
    return "pong", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# Helper function to get human members from chat
async def get_human_members(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Get list of human members (excluding bots) from the chat."""
    try:
        members = []
        administrators = await context.bot.get_chat_administrators(chat_id)
        for admin in administrators:
            if not admin.user.is_bot:
                members.append(admin.user)
        return members
    except Exception as e:
        logger.error(f"Error getting members: {e}")
        return []

def get_user_mention(user):
    """Get proper mention for user with @ and username/first name."""
    if user.username:
        return f"@{user.username}"
    else:
        return user.first_name

# Command: /gay
async def gay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pick a random member and assign gay percentage with funny message."""
    try:
        chat_id = update.effective_chat.id
        
        # Check if in group
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("This command only works in groups!")
            return
        
        members = await get_human_members(context, chat_id)
        
        if not members:
            # Fallback: use the user who sent the command
            members = [update.effective_user]
        
        random_user = random.choice(members)
        percentage = random.randint(0, 101)
        mention = get_user_mention(random_user)
        
        gay_messages = [
            f"🌈 {mention} is {percentage}% gay, confirmed by science 😂",
            f"🤣 Breaking news: {mention} just tested {percentage}% gay!",
            f"🌈 Gay-o-meter reading: {mention} = {percentage}%",
            f"😂 Doctors say {mention} is {percentage}% gay, no cure found yet!",
            f"📊 Lab results are in: {mention} scored {percentage}% gay",
            f"🔬 Scientists discovered {mention} is {percentage}% gay today",
            f"⚡ ALERT: {mention} registered {percentage}% on the gay scale!",
            f"🎯 {mention} hit {percentage}% gay accuracy rate",
            f"💯 {mention} is officially {percentage}% gay according to NASA",
            f"🌟 {mention}'s gay levels reached {percentage}% this morning"
        ]
        
        message = random.choice(gay_messages)
        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error in gay_command: {e}")
        try:
            await update.message.reply_text("Oops! Something went wrong 😅")
        except:
            pass

# Command: /couple
async def couple_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pick two random members as couple of the day with playful message."""
    try:
        chat_id = update.effective_chat.id
        
        # Check if in group
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("This command only works in groups!")
            return
        
        members = await get_human_members(context, chat_id)
        
        if len(members) < 2:
            await update.message.reply_text("Not enough members to make a couple! 😅")
            return
        
        couple = random.sample(members, 2)
        user1_mention = get_user_mention(couple[0])
        user2_mention = get_user_mention(couple[1])
        
        couple_messages = [
            f"💞 Couple of the day: {user1_mention} ❤️ {user2_mention}",
            f"😍 Wedding bells ringing for {user1_mention} + {user2_mention} 💍",
            f"🔥 Hottest duo spotted: {user1_mention} & {user2_mention}",
            f"😂 Plot twist: {user1_mention} secretly loves {user2_mention}",
            f"💘 Love is in the air! {user1_mention} 💕 {user2_mention}",
            f"✨ Match made in heaven: {user1_mention} × {user2_mention}",
            f"🎊 Congratulations! {user1_mention} and {user2_mention} are now dating",
            f"💑 {user1_mention} + {user2_mention} = Perfect couple alert!",
            f"🌹 Roses are red for {user1_mention} and {user2_mention} today",
            f"😏 Everyone ships {user1_mention} with {user2_mention}"
        ]
        
        message = random.choice(couple_messages)
        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error in couple_command: {e}")
        try:
            await update.message.reply_text("Oops! Something went wrong 😅")
        except:
            pass

# Command: /cringe
async def cringe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pick a random member and roast them with cringe message."""
    try:
        chat_id = update.effective_chat.id
        
        # Check if in group
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("This command only works in groups!")
            return
        
        members = await get_human_members(context, chat_id)
        
        if not members:
            # Fallback: use the user who sent the command
            members = [update.effective_user]
        
        random_user = random.choice(members)
        mention = get_user_mention(random_user)
        
        cringe_messages = [
            f"🤡 {mention} just unlocked *Maximum Cringe Mode* 🚨",
            f"🥴 Warning: {mention} is too cringe for this group!",
            f"😂 Bruh… {mention} really out-cringed everyone today!",
            f"📉 Cringe stock just crashed thanks to {mention}",
            f"😬 Somebody stop {mention}, the cringe levels are dangerous!",
            f"💀 {mention} is the CEO of Cringe Inc.",
            f"🙈 {mention} made everyone cringe so hard we felt it physically",
            f"⚠️ CRINGE ALERT: {mention} has entered the chat",
            f"🤢 {mention}'s cringe level is breaking all records!",
            f"🚨 Emergency: {mention} caused a cringe tsunami",
            f"😵 {mention} just weaponized cringe",
            f"🎭 {mention} won the Oscar for Best Cringe Performance",
            f"📢 PSA: {mention} is a certified cringe master",
            f"💥 {mention}'s cringe energy could power a city"
        ]
        
        message = random.choice(cringe_messages)
        await update.message.reply_text(message, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in cringe_command: {e}")
        try:
            await update.message.reply_text("Oops! Something went wrong 😅")
        except:
            pass

# Command: /chammar
async def chammar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Always reply with SHAKTI."""
    try:
        await update.message.reply_text("SHAKTI 💪🔥")
    except Exception as e:
        logger.error(f"Error in chammar_command: {e}")

# Command: /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available commands."""
    try:
        help_text = """
🤖 *Available Commands:*

/gay - Pick a random member with gay percentage 🌈
/couple - Find today's couple 💞
/cringe - Roast a random member 🤡
/chammar - Get motivated 💪
/help - Show this message

_Note: Most commands work only in groups!_
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help_command: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors caused by updates but don't crash."""
    logger.error(f'Update {update} caused error {context.error}', exc_info=context.error)

def start_bot():
    """Start the bot with error recovery."""
    while True:
        try:
            # Get token from environment variable
            TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
            
            if not TOKEN:
                logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
                time.sleep(10)
                continue
            
            logger.info("Starting bot...")
            
            # Create the Application
            application = Application.builder().token(TOKEN).build()
            
            # Register command handlers
            application.add_handler(CommandHandler("gay", gay_command))
            application.add_handler(CommandHandler("couple", couple_command))
            application.add_handler(CommandHandler("cringe", cringe_command))
            application.add_handler(CommandHandler("chammar", chammar_command))
            application.add_handler(CommandHandler("help", help_command))
            
            # Register error handler
            application.add_error_handler(error_handler)
            
            # Start the Bot
            logger.info("Bot started successfully!")
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break
        except Exception as e:
            logger.error(f"Bot crashed with error: {e}")
            logger.info("Restarting bot in 5 seconds...")
            time.sleep(5)

def main():
    """Main function to start Flask and Bot."""
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started on port {}".format(os.environ.get('PORT', 10000)))
    
    # Start bot with auto-restart on crash
    start_bot()

if __name__ == '__main__':
    main()
