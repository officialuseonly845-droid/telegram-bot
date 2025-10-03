import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import random
import os

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Daily selections storage {chat_id: {command: {date: YYYY-MM-DD, user: username}}}
daily_selections = {}

def get_group_members(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Get list of non-bot members from the group"""
    try:
        # Get chat administrators and members
        admins = context.bot.get_chat_administrators(chat_id)
        members = [admin.user for admin in admins if not admin.user.is_bot]
        return members
    except Exception as e:
        logger.error(f"Error getting group members: {e}")
        return []

def get_user_display_name(user):
    """Get username or first name"""
    if user.username:
        return f"@{user.username}"
    return user.first_name

def get_daily_selection(chat_id: int, command: str, members: list):
    """Get or create daily selection for a command"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    if chat_id not in daily_selections:
        daily_selections[chat_id] = {}
    
    if command not in daily_selections[chat_id]:
        daily_selections[chat_id][command] = {}
    
    stored = daily_selections[chat_id][command]
    
    # Check if selection is from today
    if stored.get('date') == today:
        return stored.get('users')
    
    # Create new selection for today
    if command == 'couple':
        if len(members) < 2:
            return None
        selected = random.sample(members, 2)
    else:
        if len(members) < 1:
            return None
        selected = [random.choice(members)]
    
    daily_selections[chat_id][command] = {
        'date': today,
        'users': selected
    }
    
    return selected

async def is_group_chat(update: Update) -> bool:
    """Check if message is from a group"""
    return update.effective_chat.type in ['group', 'supergroup']

async def gay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gay percentage command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🌈")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'gay', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    percentage = random.randint(0, 100)
    
    responses = [
        f"🌈 {get_user_display_name(user)} is {percentage}% gay today!",
        f"🏳️‍🌈 Gay meter shows {percentage}% for {get_user_display_name(user)}!",
        f"🌈 According to my calculations, {get_user_display_name(user)} is {percentage}% gay! 💅",
        f"🏳️‍🌈 {get_user_display_name(user)} scored {percentage}% on the gay scale today!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def couple_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Couple of the day command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 💞")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if len(members) < 2:
        await update.message.reply_text("Not enough members for a couple! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'couple', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user1, user2 = selected
    
    responses = [
        f"💞 Today's couple: {get_user_display_name(user1)} ❤️ {get_user_display_name(user2)}!",
        f"💑 {get_user_display_name(user1)} and {get_user_display_name(user2)} are the couple of the day!",
        f"❤️ Love is in the air! {get_user_display_name(user1)} × {get_user_display_name(user2)} 💕",
        f"💘 {get_user_display_name(user1)} + {get_user_display_name(user2)} = Perfect match! 💖",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def cringe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cringe roast command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🤡")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'cringe', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"🤡 {get_user_display_name(user)} is the cringiest person today!",
        f"😬 Cringe level: {get_user_display_name(user)} - MAXIMUM!",
        f"🤦 {get_user_display_name(user)} makes everyone uncomfortable with their cringe!",
        f"🙈 Can't watch {get_user_display_name(user)} without cringing!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def chammar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chammar command - always replies SHAKTI"""
    responses = [
        "SHAKTI 💪🔥",
        "💪 SHAKTI 🔥",
        "🔥 SHAKTI 💪",
        "⚡ SHAKTI POWER! 💪🔥",
    ]
    await update.message.reply_text(random.choice(responses))

async def roast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Savage roast command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🤣")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'roast', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"🔥 {get_user_display_name(user)} got roasted harder than a marshmallow!",
        f"🤣 {get_user_display_name(user)}, even your selfies know they deserve better!",
        f"😂 {get_user_display_name(user)} is the reason shampoo has instructions!",
        f"💀 {get_user_display_name(user)}, your birth certificate is an apology letter!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def simp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simp of the day command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🌚")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'simp', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"🌚 Today's simp award goes to {get_user_display_name(user)}!",
        f"😳 {get_user_display_name(user)} is the biggest simp in the chat!",
        f"💸 {get_user_display_name(user)} simping so hard today!",
        f"🤡 {get_user_display_name(user)} needs to stop simping fr!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def legend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legend of the day command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 😎")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'legend', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"😎 {get_user_display_name(user)} is today's group legend!",
        f"🏆 All hail {get_user_display_name(user)}, the legend!",
        f"👑 {get_user_display_name(user)} is an absolute legend today!",
        f"⭐ {get_user_display_name(user)} - LEGENDARY STATUS ACHIEVED!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def noob_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Noob of the day command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 😂")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'noob', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"😂 {get_user_display_name(user)} is today's certified noob!",
        f"🤦 {get_user_display_name(user)} wins the noob award!",
        f"😅 {get_user_display_name(user)} - professional noob!",
        f"🙃 {get_user_display_name(user)} is the noobiest noob today!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def luck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Luck rating command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🎲")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'luck', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    luck_level = random.randint(0, 100)
    
    responses = [
        f"🎲 {get_user_display_name(user)} has {luck_level}% luck today!",
        f"🍀 Luck meter: {get_user_display_name(user)} - {luck_level}%!",
        f"🎰 {get_user_display_name(user)}'s luck level: {luck_level}%!",
        f"✨ {get_user_display_name(user)} is {luck_level}% lucky today!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def dance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dance command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 💃")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'dance', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"💃 {get_user_display_name(user)} is dancing like nobody's watching!",
        f"🕺 {get_user_display_name(user)} got the moves today!",
        f"💃🕺 {get_user_display_name(user)} is tearing up the dance floor!",
        f"🎵 {get_user_display_name(user)} can't stop dancing!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def brain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Brain power rating command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🤯")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'brain', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    brain_level = random.randint(0, 100)
    
    responses = [
        f"🤯 {get_user_display_name(user)} has {brain_level}% brainpower today!",
        f"🧠 Brain level: {get_user_display_name(user)} - {brain_level}%!",
        f"🤓 {get_user_display_name(user)}'s IQ meter: {brain_level}%!",
        f"💡 {get_user_display_name(user)} is using {brain_level}% of their brain!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def sleep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sleepyhead command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 💤")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'sleep', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"💤 {get_user_display_name(user)} is the sleepyhead of the day!",
        f"😴 {get_user_display_name(user)} can't stop yawning today!",
        f"🛌 {get_user_display_name(user)} needs more sleep!",
        f"😪 {get_user_display_name(user)} is sleep-deprived!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def foodie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foodie command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🍕")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'foodie', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"🍕 {get_user_display_name(user)} is today's foodie champion!",
        f"🍔 {get_user_display_name(user)} is always hungry!",
        f"🍰 {get_user_display_name(user)} lives for food!",
        f"🌮 {get_user_display_name(user)} can't stop eating today!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def dead_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dead/done command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 💀")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'dead', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"💀 {get_user_display_name(user)} is done for today!",
        f"☠️ {get_user_display_name(user)} has been eliminated!",
        f"💀 RIP {get_user_display_name(user)}!",
        f"⚰️ {get_user_display_name(user)} is dead inside!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def monkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monkey command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🙈")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'monkey', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"🙈 {get_user_display_name(user)} is today's group monkey!",
        f"🐵 {get_user_display_name(user)} is going bananas!",
        f"🍌 {get_user_display_name(user)} - monkeying around!",
        f"🙊 {get_user_display_name(user)} is the monkey of the day!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def cap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cap/lying command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🧢")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'cap', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"🧢 {get_user_display_name(user)} is capping hard today!",
        f"🤥 {get_user_display_name(user)} stop the cap!",
        f"🧢 {get_user_display_name(user)} - biggest liar of the day!",
        f"🎩 {get_user_display_name(user)} is wearing the cap!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def sus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Suspicious command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🚨")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'sus', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"🚨 {get_user_display_name(user)} is acting sus today!",
        f"👀 {get_user_display_name(user)} looking real suspicious!",
        f"🤨 {get_user_display_name(user)} is the imposter!",
        f"🚩 {get_user_display_name(user)} - major red flags!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Random rating command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 🤡")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'random', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    rating = random.randint(0, 100)
    
    responses = [
        f"🤡 {get_user_display_name(user)} scored {rating}% on the silly meter!",
        f"🎲 Random rating for {get_user_display_name(user)}: {rating}%!",
        f"🎪 {get_user_display_name(user)} is {rating}% chaotic today!",
        f"🤪 {get_user_display_name(user)} got a {rating}% weirdness rating!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def mirror_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mirror roast command"""
    if not await is_group_chat(update):
        await update.message.reply_text("This command only works in groups! 😬")
        return
    
    members = get_group_members(context, update.effective_chat.id)
    if not members:
        await update.message.reply_text("Couldn't get group members! 😅")
        return
    
    selected = get_daily_selection(update.effective_chat.id, 'mirror', members)
    if not selected:
        await update.message.reply_text("Not enough members in the group! 😅")
        return
    
    user = selected[0]
    
    responses = [
        f"😬 {get_user_display_name(user)}'s mirror is filing a complaint!",
        f"🪞 The mirror cracked when {get_user_display_name(user)} looked at it!",
        f"😰 {get_user_display_name(user)}'s reflection ran away!",
        f"🙈 Even the mirror can't handle {get_user_display_name(user)}!",
    ]
    
    await update.message.reply_text(random.choice(responses))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message with all commands"""
    help_text = """
🤖 **FUN BOT COMMANDS** 🤖

🌈 /gay - Picks a random member and shows gay percentage
💞 /couple - Picks two random members as couple of the day
🤡 /cringe - Roasts a random member with cringe
💪 /chammar - SHAKTI! 🔥
🤣 /roast - Drops a savage roast on someone
🌚 /simp - Exposes the simp of the day
😎 /legend - Declares today's group legend
😂 /noob - Calls out the noob of the day
🎲 /luck - Gives a random luck rating
💃 /dance - Shows a random member dancing
🤯 /brain - Rates someone's brainpower
💤 /sleep - Marks the sleepyhead
🍕 /foodie - Picks today's foodie
💀 /dead - Declares someone "done"
🙈 /monkey - Tags the group monkey
🧢 /cap - Calls out someone lying
🚨 /sus - Marks someone suspicious
🤡 /random - Gives a random silly rating
😬 /mirror - Roasts someone's reflection
❓ /help - Shows this help message

**Note:** Most commands work only in groups and select the same person for 24 hours! 🎯
    """
    await update.message.reply_text(help_text)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    await update.message.reply_text(
        "👋 Hi! I'm the Fun Bot!\n\n"
        "Add me to a group and use /help to see all available commands!\n\n"
        "Let's make your group more fun! 🎉"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Start the bot"""
    # Get token from environment variable
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in environment variables!")
        return
    
    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("gay", gay_command))
    application.add_handler(CommandHandler("couple", couple_command))
    application.add_handler(CommandHandler("cringe", cringe_command))
    application.add_handler(CommandHandler("chammar", chammar_command))
    application.add_handler(CommandHandler("roast", roast_command))
    application.add_handler(CommandHandler("simp", simp_command))
    application.add_handler(CommandHandler("legend", legend_command))
    application.add_handler(CommandHandler("noob", noob_command))
    application.add_handler(CommandHandler("luck", luck_command))
    application.add_handler(CommandHandler("dance", dance_command))
    application.add_handler(CommandHandler("brain", brain_command))
    application.add_handler(CommandHandler("sleep", sleep_command))
    application.add_handler(CommandHandler("foodie", foodie_command))
    application.add_handler(CommandHandler("dead", dead_command))
    application.add_handler(CommandHandler("monkey", monkey_command))
    application.add_handler(CommandHandler("cap", cap_command))
    application.add_handler(CommandHandler("sus", sus_command))
    application.add_handler(CommandHandler("random", random_command))
    application.add_handler(CommandHandler("mirror", mirror_command))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
