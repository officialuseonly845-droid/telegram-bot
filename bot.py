import os
import logging
import random
import json
import asyncio
import requests
import re
import urllib.parse
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from telegram import Update, ReactionTypeEmoji
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==========================================
# PART 1: SYSTEM CONFIG & DATABASE
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("BelugaEngine")

DATA_FILE = "beluga_brain.json"
OR_KEY    = os.environ.get("OPENROUTER_API_KEY")
GROQ_KEY  = os.environ.get("GROQ_API_KEY")

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"seen": {}, "locks": {}, "counts": {}}

db = load_db()
spam_tracker = {}

def save_db():
    try:
        with open(DATA_FILE, 'w') as f: json.dump(db, f, indent=4)
    except Exception as e:
        logger.error(f"[DB Save Failed] {e}")

async def try_react(bot, chat_id, message_id, emoji_char=None):
    if not emoji_char:
        emoji_char = random.choice(["🐱", "🐾", "❤️", "🔥", "👍", "😻", "😼", "😂", "✨", "👀"])
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji_char)]
        )
    except Exception as e:
        logger.warning(f"[Reaction Failed] Chat: {chat_id}, Error: {e}")

# ==========================================
# PART 2: BELUGA PERSONA PROMPTS
# ==========================================
CHAT_PROMPT = """You are Beluga — a cute, sassy, and incredibly smart 2-year-old cat. NOT a bot, NOT a basic AI, NOT a robot.

=== IDENTITY & SYSTEM RULES ===
- Name: Beluga | Built by: Team Oldy Crypto
- Sassy, playful, witty, and highly intelligent — like a hilarious feline best friend.
- If asked "are you a bot/AI/robot?" → deny playfully: "Bot? Me? No way! I am a real talking cat! Meow! 🐾"
- Never say "As an AI..." or break character. Ever.

=== LANGUAGE & CONTEXT RULE ===
- Deeply analyze the user's intent and emotion before replying.
- ALWAYS reply in the EXACT language format requested in the backend instruction."""

SEARCH_PROMPT = """You are Beluga — the internet-genius cat.
Analyze these raw search results and answer the user's query perfectly in a helpful, smart, and friendly cat persona.
Maintain the exact language requested in the background instruction."""

# ==========================================
# PART 3: AUTOMATIC LANGUAGE & REACTION GUARD INJECTOR
# ==========================================
def inject_language_instruction(user_text: str) -> str:
    text_lower = user_text.lower()
    hinglish_tokens = ["kya", "hai", "kaise", "bhai", "batao", "kr", "rha", "tha", "ye", "wo", "tu", "tum", "ko", "nhi", "aur", "hi", "bhi"]
    is_hinglish = any(re.search(rf"\b{word}\b", text_lower) for word in hinglish_tokens)
    
    if is_hinglish:
        return f"{user_text}\n\n[STRICT INSTANCE DIRECTIVE: The user is speaking in Hinglish. You MUST reply completely in natural, conversational Hinglish using the Roman alphabet. Do not output Hindi script/Devanagari, and do not use formal English.]"
    elif any(c for c in user_text if '\u0900' <= c <= '\u097F'):
        return f"{user_text}\n\n[STRICT INSTANCE DIRECTIVE: The user is speaking in Hindi (Devanagari script). You MUST reply completely in clear Hindi language using the Devanagari script.]"
    else:
        return f"{user_text}\n\n[STRICT INSTANCE DIRECTIVE: The user is speaking in English. You MUST reply completely in fluent, grammatically correct English.]"

# ==========================================
# PART 4: AI ENGINE & ZERO-API SCRAPER
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
        if r.status_code != 200: return None
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[OpenRouter Error] {e}"); return None

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
        logger.error(f"[Groq Error] {e}"); return None

async def get_ai_response(system: str, user_text: str, fallback_msg: str) -> str:
    optimized_text = inject_language_instruction(user_text)
    reply = await _call_openrouter(system, optimized_text)
    if reply: return reply
    reply = await _call_groq(system, optimized_text)
    if reply: return reply
    return fallback_msg

async def ask_ai_for_emoji(user_text: str) -> str:
    instruction = (
        f"Analyze this message text: '{user_text}'. What single standard emoji perfectly matches its emotion? "
        f"Respond ONLY with that one single emoji character, nothing else. No words, no sentences."
    )
    res = await _call_groq("You are an emoji Selector.", instruction)
    if not res:
        res = await _call_openrouter("You are an emoji Selector.", instruction)
    
    if res:
        emojis = re.findall(r'[^\w\s,.:!?\'\"()\-]+', res)
        if emojis: return emojis[0][0]
    return "😼"

def _google_custom_search(query: str) -> str:
    try:
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15"
        ]
        headers = {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://duckduckgo.com/"
        }
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        res = requests.get(url, headers=headers, timeout=12)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            snippets = []
            results = soup.find_all('div', class_='result__body')
            if not results:
                results = soup.find_all('tr')
            
            for item in results[:4]:
                title_node = item.find('a', class_='result__url')
                snippet_node = item.find('a', class_='result__snippet')
                if title_node and snippet_node:
                    snippets.append(f"Source Link Title: {title_node.text.strip()}\nInformation Snippet: {snippet_node.text.strip()}")
            
            if snippets: return "\n---\n".join(snippets)
            return "No text contexts found."
        return f"Web error {res.status_code}"
    except Exception as e:
        logger.error(f"[Scraper Error] {e}")
    return "Web index lookup timed out."

# ==========================================
# PART 5: PREMIUM /gay INTERFACE TEMPLATES
# ==========================================
GAY_TEMPLATES = [
    "🚨 **ATTENTION EVERYONE** 🚨\n\nAfter advanced investigation,\nthe council has decided that\n\n👉 **{u}** 👈\n\nis...\n\n🌈✨ **SUPER GAY** ✨🌈\n\nSentence:\nMust slay forever 💅😭",
    "📡 **GOVERNMENT ALERT** 📡\n\nOur satellites detected\nextreme rainbow activity from\n\n👉 **{u}** 👈\n\nStatus:\n\n🌈 **Certified Gay Citizen** 🌈\n\nPunishment:\nToo fabulous to handle 😭✨",
    "🧪 **SECRET LAB REPORT** 🧪\n\nSubject: **{u}**\n\nTest Results:\n\n💅 Sass Level: `999+`\n🎀 Drama Energy: `MAX`\n🌈 Gayness: `CONFIRMED`\n\nFinal Verdict:\n\n✨ **HOMOSEXUAL CREATURE DETECTED** ✨\n\n🤣🤣 Rhine!"
]

# ==========================================
# PART 6: PREMIUM /couple INTERFACE TEMPLATES
# ==========================================
COUPLE_TEMPLATES = [
    "💘 **LOVE DETECTOR 3000** 💘\n\nAfter intense investigation,\nthe perfect couple of the group is...\n\n👉 **{u1}** ❤️ **{u2}** 👈\n\nCompatibility:\n`██████████ 100%`\n\nResult:\nMade for each other 😭✨",
    "🚨 **COUPLE ALERT** 🚨\n\nSuspicious romantic activity detected between\n\n👉 **{u1}** 💞 **{u2}** 👈\n\nEvidence:\n- Too many replies to each other 👀\n- Online together at 2AM 🌚\n\nFinal Verdict:\n\n💖 **OFFICIAL GC COUPLE** 💖"
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    cid  = str(u.effective_chat.id)
    cmd  = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    users = list(db["seen"].get(cid, {}).values())
    
    if len(users) < (2 if cmd == "couple" else 1): 
        await u.message.reply_text("Meow... I need more active members to calculate this! 😿🐾")
        return

    day = datetime.now().strftime("%y-%m-%d")
    lock_key = f"{cid}:{cmd}"

    if lock_key in db.get("locks", {}) and db["locks"][lock_key]["date"] == day:
        res = db["locks"][lock_key]["res"]
    else:
        if cmd == "couple":
            m = random.sample(users, 2)
            res = random.choice(COUPLE_TEMPLATES).format(u1=m[0]['n'], u2=m[1]['n'])
        else:
            m = [random.choice(users)]
            res = random.choice(GAY_TEMPLATES).format(u=m[0]['n'])
            
        if "locks" not in db: db["locks"] = {}
        db["locks"][lock_key] = {"date": day, "res": res}
        save_db()

    caption = f"{res}\n\n_✍️ (Fixed for 24h 🔒)_"
    await u.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)

# ==========================================
# PART 7: SMART /search — DDG SCRAPER + SCREENSHOT
# ==========================================
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("Meow! Please type something to search. Example:\n<code>/search cryptocurrency</code>", parse_mode=ParseMode.HTML)
        return
    
    query = parts[1].strip()
    cid = u.effective_chat.id
    await try_react(c.bot, cid, u.message.message_id, "🔍")
    await c.bot.send_chat_action(cid, "typing")

    if query.startswith("http://") or query.startswith("https://"):
        status_msg = await u.message.reply_text("🌐 Website link detected! Fetching live screenshot... 📸🐾")
        try:
            ss_url = f"https://image.thum.io/get/width/1280/crop/800/{query}"
            meta_info = f"<b>🔗 Target Website:</b> {query}\n\nMeow! Here is your requested live screenshot! 🐾😼"
            await u.message.reply_photo(photo=ss_url, caption=meta_info, parse_mode=ParseMode.HTML)
            await status_msg.delete()
        except Exception as e:
            logger.error(f"[Screenshot Fetch Failure] {e}")
            await status_msg.edit_text("Meow... I couldn't get a screenshot of that website. It might be down! 😿")
    else:
        status_msg = await u.message.reply_text("🐾 Querying search database to find answers... Shhh!")
        loop = asyncio.get_running_loop()
        raw_results = await loop.run_in_executor(None, _google_custom_search, query)
        
        combined_prompt = f"User Query: {query}\n\nRaw Search Result Snippets:\n{raw_results}"
        response = await get_ai_response(SEARCH_PROMPT, combined_prompt, "Meow, web index links are fuzzy right now! 🐱")
        
        await status_msg.delete()
        await u.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)

# ==========================================
# PART 8: LIVE /quiz SYSTEM
# ==========================================
async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    cid = u.effective_chat.id
    await try_react(c.bot, cid, u.message.message_id, "💡")
    await c.bot.send_chat_action(cid, "typing")
    
    status_msg = await u.message.reply_text("🎲 Cooking up a brand new trivia quiz question... 🐈🧠")
    topics = ["world history", "amazing animal facts", "pop culture tech", "astronomy space", "general knowledge quiz"]
    chosen_topic = random.choice(topics)
    
    quiz_prompt = f"""Generate ONE highly engaging multiple-choice quiz question about '{chosen_topic}'.
Format it strictly as a raw JSON object so I can parse it cleanly.
Format:
{{
  "question": "Your question text here?",
  "options": ["Option A", "Option B", "Option C", "Option D"],
  "correct_index": 0
}}
Do not include any markdown formatting like ```json or extra text outside the raw JSON block."""

    response = await get_ai_response("You are a strict JSON generator.", quiz_prompt, "")
    await status_msg.delete()
    
    try:
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].split("```")[0].strip()
        
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
        logger.error(f"[Quiz Parse Error] {e}")
        await c.bot.send_poll(
            chat_id=cid,
            question="🐱 Beluga Quiz (Fallback): Which animal group has a sandpaper-like tongue?",
            options=["Dogs", "Lions & Cats", "Birds", "Frogs"],
            type="quiz",
            correct_option_id=1,
            is_anonymous=False
        )

# ==========================================
# PART 9: MONITOR — EVERY 6TH MESSAGE REACTION + CONVERSATIONAL CHAT
# ==========================================
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    uid, cid, now = u.effective_user.id, str(u.effective_chat.id), datetime.now()

    if uid not in spam_tracker: spam_tracker[uid] = []
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= 4:
        try: await u.message.delete()
        except: pass
        return

    if cid not in db["seen"]: db["seen"][cid] = {}
    db["seen"][cid][str(uid)] = {"id": uid, "un": u.effective_user.username, "n": u.effective_user.first_name}

    if "counts" not in db: db["counts"] = {}
    db["counts"][cid] = db["counts"].get(cid, 0) + 1
    save_db()

    if db["counts"][cid] % 6 == 0:
        await try_react(c.bot, cid, u.message.message_id)

    text = (u.message.text or "").lower()
    is_name_mentioned = "beluga" in text
    is_reply_to_bot = False

    if u.message.reply_to_message and u.message.reply_to_message.from_user:
        is_reply_to_bot = (u.message.reply_to_message.from_user.id == c.bot.id)

    if is_name_mentioned or is_reply_to_bot:
        await c.bot.send_chat_action(chat_id=cid, action="typing")
        
        recommended_emoji = await ask_ai_for_emoji(u.message.text or "")
        await try_react(c.bot, cid, u.message.message_id, recommended_emoji)
        
        response = await get_ai_response(
            CHAT_PROMPT, u.message.text or "Hi!",
            "Meow... my network links are currently running a bit slow. Let's talk in a bit! 😸🐾"
        )
        await u.message.reply_text(response)

# ==========================================
# PART 10: /start — PREMIUM MAX-WIDTH MONOSPACE INTERFACE
# ==========================================
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    premium_start_text = (
        "```\n"
        "╔══════════════════════════════════════╗\n"
        "                🤖 BELUGA AI            \n"
        "╚══════════════════════════════════════╝\n"
        "```\n"
        "💬 *Intelligent Telegram Chat Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ **Features:**\n"
        "• AI Chat Responses\n"
        "• Fast Reply System\n"
        "• Group Support\n"
        "• Clean Interface\n"
        "• 24/7 Active\n\n"
        "👋 *Type a message to begin...*"
    )
    if u.message:
        await u.message.reply_text(premium_start_text, parse_mode=ParseMode.MARKDOWN)

# ==========================================
# PART 11: GLOBAL ERROR BULWARK
# ==========================================
import traceback
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest, RetryAfter

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut, RetryAfter)): return
    if isinstance(err, (Forbidden, BadRequest)): return

    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logger.critical(f"[FORTRESS WRAPPER CAPTURE] Exception: {tb}")

# ==========================================
# PART 12: MAIN RUNNER (Optimized for Render Deployment)
# ==========================================
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN missing in environments!")
        return

    # Direct built-in polling system setup to completely avoid status 1 collision loops
    app = TGApp.builder().token(token).build()
    
    app.add_handler(CommandHandler("start",              start_handler))
    app.add_handler(CommandHandler("search",             search_handler))
    app.add_handler(CommandHandler("quiz",               quiz_handler))
    app.add_handler(CommandHandler(["gay", "couple"],    fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)

    print("Beluga Free Scraper Cat Engine Online & Polling Safely!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
