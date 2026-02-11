import os, logging, random, html, json, asyncio, requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# --- API & Config ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]
NAUGHTY_PHOTOS = ["https://docs.google.com/uc?export=download&id=1ha0a76nLE61Wkl-GTChueWzFzBzg9Evm"]

# --- Persistence ---
DATA_FILE = "miko_final_data.json"
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: return {"daily": {}, "seen": {}, "config": {"model": "auto"}}
    return {"daily": {}, "seen": {}, "config": {"model": "auto"}}

data_store = load_data()
daily_locks, seen_users, config = data_store["daily"], data_store["seen"], data_store["config"]
games, naughty_index = {}, {}

def save_data():
    with open(DATA_FILE, 'w') as f: json.dump(data_store, f)

# --- AI Logic (Liquid LFM) ---
SYSTEM_PROMPT = "Name: Miko. Female, 20-24. Persona: Cute, witty, teasing, Hinglish. Short sentences. Use ✨😊💫."

async def get_miko_reply(text):
    mode = config.get("model", "auto")
    def call_api(url, key, model):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]}, timeout=8)
            return r.json()['choices'][0]['message']['content']
        except: return None

    if mode == "opr": return call_api("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "liquid/lfm-40b") or "OpenRouter Down! ✨"
    if mode == "gro": return call_api("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, "mixtral-8x7b-32768") or "Groq Down! ✨"
    return call_api("https://openrouter.ai/api/v1/chat/completions", OPENROUTER_KEY, "liquid/lfm-40b") or call_api("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, "mixtral-8x7b-32768") or "Miko is sleeping... 😴"

# --- 78+ REPLIES DATABASE (RESTORED) ---
MAPPING = {
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
        "💩 <b>{u}</b>'s birth certificate is an apology from the factory! 👶", "🛑 Stop talking, <b>{u}</b>, you're lowering the IQ of the group! 📉"
    ],
    "chammar": [
        "🚽 <b>Shakti</b> (Harpic CEO) spotted! 🧴", "🧹 <b>Shakti</b>'s mop is smarter than them! 🏆", "🧴 Harpic Blue for <b>Shakti</b>! 🧼", 
        "🪠 <b>Shakti</b>, Sultan of Sewage! 🚽", "🧼 <b>Shakti</b>, wash the floor! {p}% done! 🧹", "🪣 <b>Shakti</b>'s bucket list is just a bucket! 🪣",
        "🧹 Olympic Mop Winner: <b>Shakti</b>! 🥇", "🚽 <b>Shakti</b>'s kingdom is the urinal! 🏰"
    ],
    "aura": [
        "✨ <b>{u}</b>'s Aura: {p}% 👑", "📉 -{p} Aura for <b>{u}</b>! 💀", "🌟 Glowing at {p}%! 🌌", "🗿 Chad Aura: <b>{u}</b> ({p}%)! 🗿",
        "🤡 Clown Aura: <b>{u}</b> ({p}%)! 🎪", "💎 Diamond Aura: <b>{u}</b> ({p}%)! ✨"
    ],
    "monkey": [
        "🐒 <b>{u}</b> is {p}% Gorilla! 🦍", "🍌 Banana lover: <b>{u}</b>! 🐵", "🐒 Jungle king: <b>{u}</b>! ({p}%) 🌲",
        "🦧 <b>{u}</b> is a pure Orangutan! 🐵", "🐒 Monkey business detected! 🍌"
    ],
    "couple": [
        "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding bells: <b>{u1}</b> & <b>{u2}</b>! {p}% 🔔", "🔥 Toxic goals: <b>{u1}</b> & <b>{u2}</b>! {p}% ☢️",
        "💕 Rab Ne Bana Di Jodi: <b>{u1}</b> & <b>{u2}</b>! ({p}%) 🥰", "💔 Breakup loading for <b>{u1}</b> & <b>{u2}</b>! {p}% 📉"
    ],
    "brain": [
        "🧠 <b>{u}</b>'s Brain: {p}% 🔋", "💡 Intelligence: <b>{u}</b> ({p}%)! 🕯️", "🥔 Potato Brain: <b>{u}</b> ({p}%)! 🥔", "🧠 Big Brain Energy: <b>{u}</b> ({p}%)! ⚡"
    ]
}

# --- Handlers ---
async def miko_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("This command is meant for owner only 💋")
    kb = [[InlineKeyboardButton("OpenRouter 💎", callback_data="cfg_opr"), InlineKeyboardButton("Groq ⚡", callback_data="cfg_gro")],
          [InlineKeyboardButton("Auto Switch 🔄", callback_data="cfg_auto")]]
    await update.message.reply_text("🛠 <b>Miko Model Settings</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def fun_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.lower().split()[0].replace('/', '').split('@')[0]
    cid, today = str(update.effective_chat.id), str(datetime.now().date())
    if cid not in daily_locks or daily_locks[cid].get("date") != today: daily_locks[cid] = {"date": today, "cmds": {}}
    if cmd in daily_locks[cid]["cmds"]: return await update.message.reply_text(f"📌 {daily_locks[cid]['cmds'][cmd]}", parse_mode=ParseMode.HTML)
    
    users = list(seen_users.get(cid, {}).values())
    if not users: return await update.message.reply_text("Group mein bakchodi karo pehle! 🤡")
    
    if cmd == "couple":
        m = random.sample(users, 2) if len(users) >= 2 else users*2
        res = random.choice(MAPPING[cmd]).format(u1=m[0]['n'], u2=m[1]['n'], p=random.randint(1, 100))
    else:
        m = random.choice(users); res = random.choice(MAPPING[cmd]).format(u=m['n'], p=random.randint(0, 100))
    
    daily_locks[cid]["cmds"][cmd] = res; save_data()
    await update.message.reply_text(f"✨ {res}", parse_mode=ParseMode.HTML)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.data.startswith("cfg_"):
        if q.from_user.id not in ADMIN_IDS: return await q.answer("This command is meant for owner only 💋", show_alert=True)
        config["model"] = q.data.split("_")[1]; save_data()
        await q.edit_message_text(f"✅ Model set to: {config['model'].upper()}")
    # ... TicTac & Naughty logic yahan pichle code ki tarah ...

async def auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if f"@{context.bot.username.lower()}" in update.message.text.lower() or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
        res = await get_miko_reply(update.message.text); await update.message.reply_text(res)

async def tracker(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.effective_user or u.effective_user.is_bot: return
    cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
    if cid not in seen_users: seen_users[cid] = {}
    seen_users[cid][uid] = {"n": u.effective_user.first_name}; save_data()

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("miko", miko_admin_handler))
    for c in MAPPING.keys(): app.add_handler(CommandHandler(c, fun_dispatcher))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply))
    app.add_handler(MessageHandler(filters.ALL, tracker), group=1)
    app.run_polling()

if __name__ == '__main__': main()
