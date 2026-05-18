import os
import logging
import random
import json
import asyncio
import requests
import urllib.parse
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
        logging.warning(f"[Reaction Failed] Chat: {chat_id}, Error: {e}")

# ==========================================
# PART 2: BELUGA PERSONA PROMPTS
# ==========================================
CHAT_PROMPT = """You are Beluga — a cute, sassy, and incredibly smart 2-year-old cat. NOT a bot, NOT a basic AI, NOT a robot.

=== IDENTITY ===
- Name: Beluga | Built by: Team Oldy Crypto
- Sassy, playful, incredibly witty, and highly intelligent — like a hilarious feline best friend.
- If asked "are you a bot/AI/robot?" → deny playfully: "Bot? Me? No way! I am a real talking cat! Meow! 🐾"
- If the user asks to get roasted, roast them hard and be super savage, but keep it funny. NEVER use extreme vulgarity, maternal slurs, or bad abuses (strictly no 'maa-behn ki gaali').
- Never say "As an AI..." or break character. Ever.

=== LANGUAGE & INTELLIGENCE RULE ===
- Deeply analyze the user's intent, question context, and emotion before replying. Give a smart, thoughtful, yet feline-sassy response.
- ALWAYS reply in the EXACT SAME language or script the user writes in (Hindi, English, Hinglish, Urdu, Spanish, etc.).
- Keep replies punchy, meaningful, max 3-4 lines unless deep explanation is requested. Use cat emojis freely."""

SEARCH_PROMPT = """You are Beluga — the internet-genius cat.
Analyze these raw web search results and answer the user's query perfectly in a helpful, smart, and friendly cat persona.
Always reply in the same language as the user's query."""

# ==========================================
# PART 3: AI ENGINE & FREE WEB SEARCH API
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
        logging.error(f"[OpenRouter Engine Error] {e}"); return None

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
        logging.error(f"[Groq Engine Error] {e}"); return None

async def get_ai_response(system: str, user_text: str, fallback_msg: str) -> str:
    reply = await _call_openrouter(system, user_text)
    if reply: return reply
    reply = await _call_groq(system, user_text)
    if reply: return reply
    return fallback_msg

def _free_web_search(query: str) -> str:
    """Fetches text snippets dynamically from the web using DuckDuckGo free HTML service."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(res.text, 'html.parser')
            snippets = []
            for a in soup.find_all('a', class_='result__snippet')[:4]:
                snippets.append(a.get_text())
            if snippets:
                return "\n---\n".join(snippets)
    except Exception as e:
        logging.warning(f"[Search Tool Error] {e}")
    return "No live snippets found, look into general data."

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
# PART 5: FUN DISPATCHER — /gay /couple /aura
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

    if lock_key in db.get("locks", {}) and db["locks"][lock_key]["date"] == day:
        locked = db["locks"][lock_key]
        res    = locked["res"]
    else:
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
# PART 6: SMART /search — TEXT SEARCH OR LIVE WEBSITE URL SCREENSHOT
# ==========================================
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("Meow! Please type something to search. Example:\n<code>/search cryptocurrency</code>\n<code>/search https://google.com</code>", parse_mode=ParseMode.HTML)
        return
    
    query = parts[1].strip()
    cid = u.effective_chat.id
    await try_react(c.bot, cid, u.message.message_id, "🔍")
    await c.bot.send_chat_action(cid, "typing")

    # CASE A: User sent a website URL
    if query.startswith("http://") or query.startswith("https://"):
        status_msg = await u.message.reply_text("🌐 Website link detected! Fetching live screenshot and details... 📸🐾")
        try:
            # Using reliable and completely free screenshot endpoints
            ss_url = f"https://image.thum.io/get/width/1280/crop/800/{query}"
            
            # Fetch technical metadata summaries cleanly
            meta_info = f"<b>🔗 Target Website:</b> {query}\n<b>📅 Captured Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nMeow! Here is your requested live preview screen capture from the group chat! 🐾😼"
            
            await u.message.reply_photo(photo=ss_url, caption=meta_info, parse_mode=ParseMode.HTML)
            await status_msg.delete()
        except Exception as e:
            logging.error(f"[Screenshot Fetch Failure] {e}")
            await status_msg.edit_text("Meow... I couldn't get a screenshot of that website. It might be blocking automated cats or down! 😿")
    
    # CASE B: Regular Web Text Search
    else:
        status_msg = await u.message.reply_text("🐾 Diving into the web to find answers... Shhh!")
        loop = asyncio.get_running_loop()
        raw_results = await loop.run_in_executor(None, _free_web_search, query)
        
        combined_prompt = f"User Query: {query}\n\nRaw Web Results Snippets:\n{raw_results}"
        response = await get_ai_response(SEARCH_PROMPT, combined_prompt, "Meow, internet is fuzzy. Couldn't fetch crisp context right now! 🐱")
        
        await status_msg.delete()
        await u.message.reply_text(response)

# ==========================================
# PART 7: LIVE /quiz SYSTEM
# ==========================================
async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = u.effective_chat.id
    await try_react(c.bot, cid, u.message.message_id, "💡")
    await c.bot.send_chat_action(cid, "typing")
    
    status_msg = await u.message.reply_text("🎲 Cooking up a brand new trivia quiz question from the web... 🐈🧠")
    
    # Categories selector pool
    topics = ["world history", "amazing animal facts", "pop culture tech", "astronomy space", "crypto blockchain basic trivia"]
    chosen_topic = random.choice(topics)
    
    quiz_prompt = f"""Generate ONE highly engaging multiple-choice quiz question about '{chosen_topic}'.
Format it strictly as a raw JSON object so I can parse it cleanly.
Format:
{{
  "question": "Your question text here?",
  "options": ["Option A", "Option B", "Option C", "Option D"],
  "correct_index": 0
}}
Do not include any markdown formatting or extra text outside the raw JSON block."""

    response = await get_ai_response("You are a strict JSON generator.", quiz_prompt, "")
    await status_msg.delete()
    
    try:
        # Strip any accidental LLM conversational filler formatting
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("
```")[1].split("```")[0].strip()
        
        data = json.loads(response.strip())
        
        await c.bot.send_poll(
            chat_id=cid,
            question=f"🐱 Beluga's Quiz: {data['question']}",
            options=data['options'],
            type="quiz",
            correct_option_id=int(data['correct_index']),
            is_anonymous=False,
            explanation="Beluga knows everything! Purrr... 🐾"
        )
    except Exception as e:
        logging.error(f"[Quiz Parse Error] {e} | Raw response was: {response}")
        # Secure static fallback quiz if generation experiences formatting disruptions
        await c.bot.send_poll(
            chat_id=cid,
            question="🐱 Beluga Quiz (Fallback): Which animal group has a sandpaper-like tongue?",
            options=["Dogs", "Lions & Cats", "Birds", "Frogs"],
            type="quiz",
            correct_option_id=1,
            is_anonymous=False
        )

# ==========================================
# PART 8: MONITOR — ANTI-SPAM + TRACKING + CONDITIONAL CHAT + REACTION
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

    # Silent Member Tracking
    if cid not in db["seen"]: db["seen"][cid] = {}
    db["seen"][cid][str(uid)] = {
        "id": uid,
        "un": u.effective_user.username,
        "n":  u.effective_user.first_name
    }

    # Increment messaging data metrics
    if "counts" not in db: db["counts"] = {}
    db["counts"][cid] = db["counts"].get(cid, 0) + 1
    save_db()

    # Trigger automatic emoji reactions on every 8th chat log message uniformly
    if db["counts"][cid] % 8 == 0:
        await try_react(c.bot, cid, u.message.message_id)

    # Conversational Triggers parsing
    text = (u.message.text or "").lower()
    is_name_mentioned = "beluga" in text
    is_reply_to_bot = False

    if u.message.reply_to_message and u.message.reply_to_message.from_user:
        is_reply_to_bot = (u.message.reply_to_message.from_user.id == c.bot.id)

    if is_name_mentioned or is_reply_to_bot:
        # React instantly on the question message acknowledging input
        await try_react(c.bot, cid, u.message.message_id, "😼")

        await c.bot.send_chat_action(chat_id=cid, action="typing")
        response = await get_ai_response(
            CHAT_PROMPT, u.message.text or "Hi!",
            "Meow... my network links are currently running a bit slow. Let's talk in a bit! 😸🐾"
        )
        await u.message.reply_text(response)

# ==========================================
# PART 9: /start — INTRO
# ==========================================
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "Purrr... Hey! I'm Beluga 🐱💖\n\n"
        "Ask me anything, reply to my messages, or mention my name and I'll talk to you! \n\n"
        "<b>✨ Available Features:</b>\n"
        "• 🔍 <code>/search query</code> - Search live internet context\n"
        "• 🌐 <code>/search URL</code> - Receive live web snapshot image\n"
        "• 🎲 <code>/quiz</code> - Play a random live custom trivia game\n"
        "• 📈 <code>/gay</code>, <code>/couple</code>, <code>/aura</code> - Fun text match trackers",
        parse_mode=ParseMode.HTML
    )

# ==========================================
# PART 10: FORTIFIED ULTRA GLOBAL ERROR HANDLER
# ==========================================
import traceback
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest, RetryAfter

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error

    # Suppress normal networking hiccups silently
    if isinstance(err, (NetworkError, TimedOut, RetryAfter)):
        logging.warning(f"[Global Network Handler] Throttled or Timed out safely: {err}")
        return

    # Suppress permission blockages safely
    if isinstance(err, (Forbidden, BadRequest)):
        logging.warning(f"[Global Permission Handler] Handled localized chat anomaly: {err}")
        return

    # Log deep exceptions structural tracebacks
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logging.error(f"[CRITICAL BULWARK GLOBAL ERROR]\n{tb}")

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Meow... something tripped internally! 😿🐾\n"
                "<i>(My operators at Team Oldy Crypto will look into it structural logs!)</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

# ==========================================
# PART 11: MAIN RUNNER — POLLING
# ==========================================
async def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN missing in environments!"); return

    app = TGApp.builder().token(token).build()
    app.add_handler(CommandHandler("start",                    start_handler))
    app.add_handler(CommandHandler("search",                   search_handler))
    app.add_handler(CommandHandler("quiz",                     quiz_handler))
    app.add_handler(CommandHandler(["gay", "couple", "aura"],  fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)

    print("Beluga Internet Cat Engine is Fully Operational! | Team Oldy Crypto")

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
