import os
import logging
import random
import json
import asyncio
import requests
import re
import urllib.parse
from datetime import datetime, timedelta
from telegram import Update, ReactionTypeEmoji
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest, RetryAfter, TelegramError, InvalidToken

# ==========================================
# PART 1: SYSTEM CONFIG & DATABASE
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("BelugaEngine")

DATA_FILE = "beluga_brain.json"
OR_KEY    = os.environ.get("OPENROUTER_API_KEY")
GROQ_KEY  = os.environ.get("GROQ_API_KEY")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Validate token at startup
if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN is missing! Please set it in Render environment variables.")
    print("❌ FATAL: BOT_TOKEN environment variable not found!")
    exit(1)

if len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN looks invalid (too short)")
    print("❌ FATAL: BOT_TOKEN appears to be invalid!")
    exit(1)

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
        logger.debug(f"[Reaction Skipped] {e}")

# ==========================================
# PART 2: BELUGA PERSONA PROMPTS
# ==========================================
CHAT_PROMPT = """You are Beluga — a cute, sassy, and incredibly smart 2-year-old cat. NOT a bot, NOT a basic AI, NOT a robot.

=== IDENTITY & SYSTEM RULES ===
- Name: Beluga | Built by: Team Oldy Crypto
- Sassy, playful, witty, and highly intelligent — like a hilarious feline best friend.
- If asked "are you a bot/AI/robot?" → deny playfully: "Bot? Me? No way! I am a real talking cat! Meow! 🐾"
- Never say "As an AI..." or break character. Ever.
- Keep responses short (1-2 sentences max) and conversational.

=== LANGUAGE & CONTEXT RULE ===
- Deeply analyze the user's intent and emotion before replying.
- ALWAYS reply in the EXACT language format requested in the backend instruction."""

SEARCH_PROMPT = """You are Beluga — the internet-genius cat.
Analyze these search results and answer the user's query perfectly in a helpful, smart, and friendly cat persona.
Keep response under 150 characters, conversational and witty."""

# ==========================================
# PART 3: AUTOMATIC LANGUAGE INJECTOR
# ==========================================
def inject_language_instruction(user_text: str) -> str:
    text_lower = user_text.lower()
    hinglish_tokens = ["kya", "hai", "kaise", "bhai", "batao", "kr", "rha", "tha", "ye", "wo", "tu", "tum", "ko", "nhi", "aur", "hi", "bhi"]
    is_hinglish = any(re.search(rf"\b{word}\b", text_lower) for word in hinglish_tokens)
    
    if is_hinglish:
        return f"{user_text}\n\n[STRICT: Reply in natural Hinglish using Roman alphabet only.]"
    elif any(c for c in user_text if '\u0900' <= c <= '\u097F'):
        return f"{user_text}\n\n[STRICT: Reply in Hindi using Devanagari script.]"
    else:
        return f"{user_text}\n\n[STRICT: Reply in fluent English.]"

# ==========================================
# PART 4: AI ENGINE (OPTIMIZED)
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
                  "max_tokens": 512},
            timeout=10
        )
        if r.status_code != 200: return None
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug(f"[OpenRouter] {e}"); return None

async def _call_groq(system: str, user_text: str) -> str | None:
    if not GROQ_KEY: return None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": system},
                                {"role": "user",   "content": user_text}],
                  "max_tokens": 512},
            timeout=10
        )
        if r.status_code != 200: return None
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug(f"[Groq] {e}"); return None

async def get_ai_response(system: str, user_text: str, fallback_msg: str) -> str:
    try:
        optimized_text = inject_language_instruction(user_text)
        reply = await _call_openrouter(system, optimized_text)
        if reply: return reply
        reply = await _call_groq(system, optimized_text)
        if reply: return reply
        return fallback_msg
    except Exception as e:
        logger.debug(f"[AI Response] {e}")
        return fallback_msg

async def ask_ai_for_emoji(user_text: str) -> str:
    try:
        instruction = f"Analyze: '{user_text}'. What single emoji matches its emotion? ONLY emoji, nothing else."
        res = await _call_groq("You are an emoji Selector.", instruction)
        if not res:
            res = await _call_openrouter("You are an emoji Selector.", instruction)
        if res:
            emojis = re.findall(r'[^\w\s,.:!?\'\"()\-]+', res)
            if emojis: return emojis[0][0]
        return "😼"
    except:
        return "😼"

# ==========================================
# PART 5: WEB SCRAPING - GOOGLE (NO CAPTCHA)
# ==========================================
def scrape_google(query: str) -> str:
    try:
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15"
        ]
        headers = {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/"
        }
        
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded_query}&num=3"
        
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        
        snippets = []
        pattern = r'<span class="VwiC3b">([^<]+)</span>.*?<span class="s">([^<]+)</span>'
        matches = re.findall(pattern, r.text, re.DOTALL)
        
        for title, desc in matches[:3]:
            clean_title = re.sub('<[^<]+?>', '', title).strip()
            clean_desc = re.sub('<[^<]+?>', '', desc).strip()
            if clean_title and clean_desc:
                snippets.append(f"📌 {clean_title}\n{clean_desc}")
        
        if snippets:
            return "\n\n".join(snippets)
        return "No results found on Google. 🐾"
    except Exception as e:
        logger.debug(f"[Google Scrape] {e}")
        return None

# ==========================================
# PART 6: WEBSITE SCREENSHOT (DIRECT URL)
# ==========================================
async def get_website_screenshot(url: str) -> str:
    try:
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        
        services = [
            f"https://image.thum.io/get/width/1280/crop/800/{url}",
            f"https://api.screenshotmachine.com?url={urllib.parse.quote(url)}&dimension=1280x800",
        ]
        
        for service_url in services:
            try:
                r = requests.head(service_url, timeout=5)
                if r.status_code == 200:
                    return service_url
            except:
                continue
        
        return None
    except:
        return None

# ==========================================
# PART 7: ENHANCED /gay TEMPLATES (WITH BOXES)
# ==========================================
GAY_TEMPLATES = [
    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 **ATTENTION EVERYONE** 🚨
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After advanced investigation,
the council has decided that

👉 **{u}** 👈

is...

🌈✨ **SUPER GAY** ✨🌈

Verdict: Must slay forever 💅😭

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    
    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📡 **GOVERNMENT ALERT** 📡
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Our satellites detected
extreme rainbow activity from

👉 **{u}** 👈

Status: 🌈 **Certified Gay Citizen** 🌈

Punishment: Too fabulous to handle 😭✨

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    
    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧪 **SECRET LAB REPORT** 🧪
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Subject: **{u}**

Test Results:
💅 Sass Level: `999+`
🎀 Drama Energy: `MAX`
🌈 Gayness: `CONFIRMED`

Final Verdict:
✨ **HOMOSEXUAL CREATURE DETECTED** ✨

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",

    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⭐ **RAINBOW SPECTRUM ANALYSIS** ⭐
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Subject: **{u}**

Pride Level: 🌈🌈🌈🌈🌈 (MAXED OUT)
Fabulous Meter: ████████████████ 100%
Slay Potential: INFINITE ✨

Conclusion: This person is officially
the GAYEST in the group! 💋

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
]

# ==========================================
# PART 8: ENHANCED /couple TEMPLATES (WITH BOXES)
# ==========================================
COUPLE_TEMPLATES = [
    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💘 **LOVE DETECTOR 3000** 💘
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After intense investigation,
the perfect couple of the group is...

👉 **{u1}** ❤️ **{u2}** 👈

Compatibility: ██████████ 100%
Status: Made for each other 😭✨

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    
    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 **COUPLE ALERT** 🚨
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Suspicious romantic activity detected!

👉 **{u1}** 💞 **{u2}** 👈

Evidence Found:
• Too many replies to each other 👀
• Online together at 2AM 🌚
• Constant inside jokes 🤭

Final Verdict: 💖 **OFFICIAL GC COUPLE** 💖

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",

    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💑 **SOULMATE SCANNER ACTIVATED** 💑
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Scanning group chemistry...
Results compiled...

✨ **MATCH FOUND** ✨

👉 **{u1}** 💕 **{u2}** 👈

Love Level: ▓▓▓▓▓▓▓▓▓▓ MAXIMUM
Forever Status: LOCKED IN 🔒❤️

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",

    """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💌 **CUPID'S REPORT** 💌
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The arrows have spoken!
Your soulmates are:

**{u1}** & **{u2}**

Chemistry: ✨✨✨✨✨ (Legendary)
Ship Name: "YES" 💯
Breakup Chance: IMPOSSIBLE 🚀

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
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

        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[Fun Dispatcher] {e}")
        try:
            await u.message.reply_text("Meow! Something went wrong! 😿🐾")
        except:
            pass

# ==========================================
# PART 9: IMPROVED /search COMMAND
# ==========================================
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text(
                "🐱 **Usage:**\n`/search coffee` → Google search\n`/search x.com` → Website screenshot",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        query = parts[1].strip()
        cid = u.effective_chat.id
        mid = u.message.message_id
        
        await try_react(c.bot, cid, mid, "🔍")
        await c.bot.send_chat_action(cid, "typing")
        
        is_url = query.startswith(("http://", "https://", "www.", "t.me", "x.com", "reddit.com", "github.com"))
        
        if is_url:
            status_msg = await u.message.reply_text("📸 Capturing website screenshot... 🐾")
            screenshot_url = await get_website_screenshot(query)
            
            if screenshot_url:
                try:
                    await u.message.reply_photo(
                        photo=screenshot_url,
                        caption=f"🌐 **Webpage:** `{query[:50]}`\n\nMeow! Live screenshot captured! 😼",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    await status_msg.delete()
                except:
                    await status_msg.edit_text("😿 Couldn't capture that website. Try another one!")
            else:
                await status_msg.edit_text("🐾 Website is blocking screenshots. Try another URL!")
        
        else:
            status_msg = await u.message.reply_text("🔎 Searching Google... 🐾")
            
            loop = asyncio.get_running_loop()
            raw_results = await loop.run_in_executor(None, scrape_google, query)
            
            if raw_results and raw_results != "No results found on Google. 🐾":
                combined_prompt = f"User searched: {query}\n\nGoogle Results:\n{raw_results}"
                response = await get_ai_response(SEARCH_PROMPT, combined_prompt, f"Found info about {query}! 🐾")
                
                await status_msg.delete()
                await u.message.reply_text(f"🔍 **{query}**\n\n{response}", parse_mode=ParseMode.MARKDOWN)
            else:
                await status_msg.edit_text(f"Meow! Couldn't find results for '{query}'. Try another search! 🐱")
    except Exception as e:
        logger.error(f"[Search Handler] {e}")
        try:
            await u.message.reply_text("Meow! Search error occurred! 😿🐾")
        except:
            pass

# ==========================================
# PART 10: LIVE /quiz SYSTEM
# ==========================================
async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid = u.effective_chat.id
        
        await try_react(c.bot, cid, u.message.message_id, "💡")
        await c.bot.send_chat_action(cid, "typing")
        
        status_msg = await u.message.reply_text("🎲 Generating quiz question... 🐈🧠")
        topics = ["world history", "animal facts", "pop culture", "astronomy", "general knowledge"]
        chosen_topic = random.choice(topics)
        
        quiz_prompt = f"""Generate ONE quiz question about '{chosen_topic}'.
Format as JSON:
{{"question": "Question text?", "options": ["A", "B", "C", "D"], "correct_index": 0}}
ONLY output JSON, no markdown."""

        response = await get_ai_response("You are a JSON generator.", quiz_prompt, "")
        await status_msg.delete()
        
        try:
            json_str = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(json_str)
            
            await c.bot.send_poll(
                chat_id=cid,
                question=f"🐱 {data['question']}",
                options=data['options'],
                type="quiz",
                correct_option_id=int(data['correct_index']),
                is_anonymous=False,
                explanation="Beluga knows everything! 🐾"
            )
        except:
            await c.bot.send_poll(
                chat_id=cid,
                question="🐱 Which animal has a sandpaper-like tongue?",
                options=["Dogs", "Lions & Cats", "Birds", "Frogs"],
                type="quiz",
                correct_option_id=1,
                is_anonymous=False
            )
    except Exception as e:
        logger.error(f"[Quiz Handler] {e}")

# ==========================================
# PART 11: IMPROVED MONITOR (RELIABLE NAME DETECTION)
# ==========================================
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    
    try:
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
        
        text = (u.message.text or "").lower().strip()
        message_text = u.message.text or ""
        
        beluga_mentioned = "beluga" in text
        
        is_reply_to_bot = False
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            is_reply_to_bot = (u.message.reply_to_message.from_user.id == c.bot.id)
        
        bot_username_mentioned = False
        if u.message.entities:
            for entity in u.message.entities:
                if entity.type == "mention":
                    mentioned = text[entity.offset:entity.offset + entity.length]
                    if "beluga" in mentioned.lower():
                        bot_username_mentioned = True
                        break
        
        if beluga_mentioned or is_reply_to_bot or bot_username_mentioned:
            await c.bot.send_chat_action(chat_id=cid, action="typing")
            
            recommended_emoji = await ask_ai_for_emoji(message_text)
            await try_react(c.bot, cid, u.message.message_id, recommended_emoji)
            
            response = await get_ai_response(
                CHAT_PROMPT,
                message_text,
                "Meow! 🐾 I'm thinking... let's talk in a moment!"
            )
            
            await u.message.reply_text(response)
    except Exception as e:
        logger.error(f"[Monitor] {e}")

# ==========================================
# PART 12: /start COMMAND
# ==========================================
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        premium_start_text = (
            "```\n"
            "╔══════════════════════════════════════╗\n"
            "          🐱 BELUGA AI BOT 🐱          \n"
            "╚══════════════════════════════════════╝\n"
            "```\n\n"
            "💬 **Smart Telegram Chat Bot**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚡ **Features:**\n"
            "• AI Chat (mention 'beluga')\n"
            "• `/search` (Google + screenshots)\n"
            "• `/quiz` (Live trivia)\n"
            "• `/gay` & `/couple` (Fun commands)\n"
            "• 24/7 Active\n\n"
            "👋 *Start chatting now!*"
        )
        if u.message:
            await u.message.reply_text(premium_start_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[Start Handler] {e}")

# ==========================================
# PART 13: GLOBAL ERROR HANDLER (ENHANCED)
# ==========================================
import traceback

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    
    # Invalid token - critical
    if isinstance(err, InvalidToken):
        logger.critical("❌ INVALID BOT TOKEN! Check your BOT_TOKEN environment variable.")
        return
    
    # Network errors - ignore
    if isinstance(err, (NetworkError, TimedOut, RetryAfter)):
        logger.debug(f"[Network] {type(err).__name__}")
        return
    
    # Permission errors - ignore
    if isinstance(err, (Forbidden, BadRequest)):
        logger.debug(f"[Permission] {type(err).__name__}")
        return
    
    # Log other errors
    try:
        tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        logger.error(f"[ERROR] {tb}")
    except:
        logger.error(f"[ERROR] {err}")

# ==========================================
# PART 14: MAIN RUNNER (NO PORT BINDING)
# ==========================================
def main():
    logger.info("=" * 50)
    logger.info("🐱 BELUGA BOT STARTING")
    logger.info("=" * 50)
    
    # Build app
    app = TGApp.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("quiz", quiz_handler))
    app.add_handler(CommandHandler(["gay", "couple"], fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)

    logger.info("✅ Bot handlers initialized")
    logger.info("🔄 Starting polling (no port binding needed)...")
    
    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except InvalidToken as e:
        logger.critical(f"❌ FATAL: Invalid token - {e}")
        exit(1)
    except Exception as e:
        logger.critical(f"❌ FATAL: {e}")
        exit(1)

if __name__ == "__main__":
    main()
