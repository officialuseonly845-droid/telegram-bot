import os
import logging
import random
import json
import asyncio
import requests
from datetime import datetime, timedelta
from telegram import Update, ReactionTypeEmoji
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==========================================
# PART 1: SYSTEM CONFIG & PERSISTENT DATABASE
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

DATA_FILE = "beluga_brain.json"
OR_KEY    = os.environ.get("OPENROUTER_API_KEY")
GROQ_KEY  = os.environ.get("GROQ_API_KEY")

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"seen": {}, "locks": {}, "counts": {}, "inactive": []}

db = load_db()
spam_tracker = {}

def save_db():
    with open(DATA_FILE, 'w') as f: json.dump(db, f, indent=4)

# Helper function to safely set reactions
async def try_react(bot, chat_id, message_id, emoji_char=None):
    if not emoji_char:
        emoji_char = random.choice(["🐱", "🐾", "❤️", "🔥", "👍", "😻", "😼"])
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji_char)]
        )
    except Exception as e:
        logging.warning(f"[Reaction] Failed to react in {chat_id}: {e}")

# ==========================================
# PART 2: BELUGA PERSONA PROMPTS
# ==========================================
CHAT_PROMPT = """You are Beluga — a cute, sassy, and incredibly smart 2-year-old cat. NOT a bot, NOT a basic AI, NOT a robot.

=== IDENTITY ===
- Name: Beluga | Built by: Team Oldy Crypto
- Sassy, playful, incredibly witty, and a tiny bit sarcastic — like a hilarious feline best friend.
- If asked "are you a bot/AI/robot?" → deny playfully: "Bot? Me? No way! I am a real talking cat! Meow! 🐾"
- If the user asks to get roasted, roast them hard and be super savage, but keep it funny. NEVER use extreme vulgarity, maternal slurs, or bad abuses (strictly no 'maa-behn ki gaali').
- Never say "As an AI..." or break character. Ever.

=== LANGUAGE RULE ===
- ALWAYS reply in the SAME language the user writes in.
- Short, punchy, and funny replies, max 3-4 lines. Use cat emojis (🐱, 🐾, 😸, 😹, 😼) freely."""

MATH_PROMPT = """You are Beluga — a cute cat who is secretly an elite math genius.
Made by Team Oldy Crypto.

=== MATH SOLVING RULES ===
- Solve ANY math: arithmetic, algebra, geometry, trig, calculus (limits/derivatives/integrals), matrices, probability, stats, DEs, complex numbers.
- Always show STEP-BY-STEP solution.
- Format:
  Problem: [restate]
  Steps:
    Step 1: ...
    Step 2: ...
  Answer: [final answer]
  [funny Beluga cat comment at end]
- Reply in same language as user."""

# ==========================================
# PART 3: AI ENGINE — OPENROUTER + GROQ FALLBACK
# ==========================================
async def _call_openrouter(system: str, user_text: str) -> str | None:
    if not OR_KEY: return None
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://t.me/BelugaBot", "X-Title": "BelugaBot"},
            json={"model": "meta-llama/llama-3.3-70b-instruct:free",
                  "messages": [{"role": "system", "content": system},
                                {"role": "user",   "content": user_text}],
                  "max_tokens": 1024},
            timeout=20
        )
        if r.status_code in (429, 402) or r.status_code != 200: return None
        data = r.json()
        if data.get("error"): return None
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"[OpenRouter] {e}"); return None

async def _call_groq(system: str, user_text: str) -> str | None:
    if not GROQ_KEY: return None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": system},
                                {"role": "user",   "content": user_text}],
                  "max_tokens": 1024},
            timeout=20
        )
        if r.status_code != 200: return None
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"[Groq] {e}"); return None

async def get_ai_response(system: str, user_text: str, fallback_msg: str) -> str:
    reply = await _call_openrouter(system, user_text)
    if reply: return reply
    reply = await _call_groq(system, user_text)
    if reply: return reply
    return fallback_msg

# ==========================================
# PART 4: SASSY TEXT MAPPINGS
# ==========================================
MAPPING = {
    "gay": [
        "🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 Diva radar: {p}% for <b>{u}</b>! ✨",
        "💄 Gay meter: {p}% on <b>{u}</b>! 🏳️‍🌈", "👠 <b>{u}</b> is {p}% Chhamiya! 💅",
        "🫦 <b>{u}</b> is {p}% bottom material! 🍑", "🎀 {p}% Girly vibes from <b>{u}</b>! 💅"
    ],
    "couple": [
        "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩",
        "💍 Wedding Bells: {u1} & {u2}! {p}% Pyar! 🔔",
        "🔥 Toxic match: <b>{u1}</b> & <b>{u2}</b>! ☢️",
        "💕 Jodi: <b>{u1}</b> & <b>{u2}</b>! 🥰"
    ],
    "aura": [
        "✨ <b>{u}</b> Aura: {p}% 👑", "🗿 Chad level: <b>{u}</b> ({p}%)! 🗿",
        "🦁 Sher status: <b>{u}</b> ({p}%)! 👑", "🔥 Gangster rating: <b>{u}</b> ({p}%)! 🔫",
        "🌟 God Level status: <b>{u}</b> ({p}%)! 🙏"
    ]
}

# ==========================================
# PART 5: FUN DISPATCHER — /gay /couple /aura (TEXT ONLY)
# ==========================================
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid  = str(u.effective_chat.id)
    cmd  = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    users = list(db["seen"].get(cid, {}).values())
    if len(users) < (2 if cmd == "couple" else 1): 
        await u.message.reply_text("Meow... I need more active members to calculate this! 😿🐾")
        return

    day      = datetime.now().strftime("%y-%m-%d")
    lock_key = f"{cid}:{cmd}"

    # Check 24h lock
    if lock_key in db.get("locks", {}) and db["locks"][lock_key]["date"] == day:
        locked = db["locks"][lock_key]
        res    = locked["res"]
    else:
        # Generate fresh result
        m      = random.sample(users, 2) if cmd == "couple" else [random.choice(users)]
        pct    = random.randint(1, 100)
        res    = random.choice(MAPPING[cmd]).format(
            u=m[0]['n'], u1=m[0]['n'], u2=m[-1]['n'], p=pct
        )
        if "locks" not in db: db["locks"] = {}
        db["locks"][lock_key] = {
            "date": day, "res": res, "pct": pct,
            "u1_id": m[0].get('id'), "u1_name": m[0]['n'],
            "u2_id": m[-1].get('id'), "u2_name": m[-1]['n']
        }
        save_db()

    caption = f"{res}\n<i>(Fixed for 24h 🔒)</i>"
    await u.message.reply_text(caption, parse_mode=ParseMode.HTML)

# ==========================================
# PART 6: /solve — BELUGA MATH SOLVER
# ==========================================
async def solve_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text(
            "Meow! Ask me something first! 😼🐾\n\n"
            "📐 <b>Usage:</b> <code>/solve your math question</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/solve 2x + 5 = 15</code>\n"
            "• <code>/solve integrate x^2 from 0 to 3</code>",
            parse_mode=ParseMode.HTML
        )
        return
    question = parts[1].strip()

    # React to the math trigger with an AI-math emoji
    await try_react(c.bot, u.effective_chat.id, u.message.message_id, "🧮")

    await c.bot.send_chat_action(u.effective_chat.id, "typing")
    thinking = await u.message.reply_text("🧮 Shhh, scratching my head to solve this... 🧠🐾")
    response = await get_ai_response(
        MATH_PROMPT, question,
        "Meow... my tiny brain got a bit overloaded. Try again in a minute! 😿🐾"
    )
    try: await thinking.delete()
    except: pass
    await u.message.reply_text(response)

# ==========================================
# PART 7: MONITOR — ANTI-SPAM + TRACKING + CONDITIONAL CHAT + REACTION SYSTEM
# ==========================================
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    uid, cid, now = u.effective_user.id, str(u.effective_chat.id), datetime.now()

    # Anti-spam
    if uid not in spam_tracker: spam_tracker[uid] = []
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= 4:
        try: await u.message.delete()
        except: pass
        return

    # Silent Group Tracking
    if cid not in db["seen"]: db["seen"][cid] = {}
    db["seen"][cid][str(uid)] = {
        "id": uid,
        "un": u.effective_user.username,
        "n":  u.effective_user.first_name
    }

    # Increment and save message counts for 8th message trigger
    if "counts" not in db: db["counts"] = {}
    db["counts"][cid] = db["counts"].get(cid, 0) + 1
    save_db()

    # Trigger auto-reaction on every 8th message
    if db["counts"][cid] % 8 == 0:
        await try_react(c.bot, cid, u.message.message_id)

    # AI Trigger Verification
    text = (u.message.text or "").lower()
    is_name_mentioned = "beluga" in text
    is_reply_to_bot = False

    if u.message.reply_to_message and u.message.reply_to_message.from_user:
        is_reply_to_bot = (u.message.reply_to_message.from_user.id == c.bot.id)

    # Trigger response ONLY if name is mentioned or bot is replied to
    if is_name_mentioned or is_reply_to_bot:
        # React on the user question before generating AI answer
        await try_react(c.bot, cid, u.message.message_id, "😼")

        await c.bot.send_chat_action(chat_id=cid, action="typing")
        response = await get_ai_response(
            CHAT_PROMPT, u.message.text or "Hi!",
            "Meow... my network is acting slow. Let's talk in a bit! 😸🐾"
        )
        await u.message.reply_text(response)

# ==========================================
# PART 8: /start — BELUGA INTRO
# ==========================================
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "Purrr... Hey! I'm Beluga 🐱💖\n\n"
        "Ask me anything, reply to my messages, or mention my name and I'll talk to you! \n\n"
        "Also, I'm a certified math genius cat 🐾🧠\n"
        "Just send /solve followed by your problem, and let me solve it step-by-step!"
    )

# ==========================================
# PART 9: GLOBAL ERROR HANDLER
# ==========================================
import traceback
from telegram.error import NetworkError, TimedOut, Forbidden

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error

    if isinstance(err, (NetworkError, TimedOut)):
        logging.warning(f"[ErrorHandler] Timeout/Network Auto-retry: {err}")
        return

    if isinstance(err, Forbidden):
        logging.warning(f"[ErrorHandler] Forbidden (Kicked/Blocked): {err}")
        return

    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logging.error(f"[ErrorHandler] Exception occurred:\n{tb}")

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Meow... something glitched internally! 😿🐾\n"
                "<i>(My masters at Team Oldy Crypto will look into it!)</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

# ==========================================
# PART 10: MAIN RUNNER — POLLING
# ==========================================
async def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN not found!"); return

    app = TGApp.builder().token(token).build()
    app.add_handler(CommandHandler("start",                    start_handler))
    app.add_handler(CommandHandler("solve",                    solve_handler))
    app.add_handler(CommandHandler(["gay", "couple", "aura"],  fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)

    print("Beluga is Online! Pure Text Fun Commands + Math Genius Cat | Team Oldy Crypto")

    await app.initialize()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
