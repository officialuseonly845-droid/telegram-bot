import os
import logging
import random
import json
import asyncio
import requests
import re
import urllib.parse
import traceback
import sys
from datetime import datetime, timedelta
from typing import Optional
from telegram import Update, ReactionTypeEmoji
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import (
    NetworkError, TimedOut, Forbidden, BadRequest,
    RetryAfter, TelegramError, InvalidToken
)

# ==========================================
# PART 0: HTTP SERVER FOR HEALTH CHECKS
# ==========================================
from aiohttp import web

# Health check variables
bot_status = {
    "running": False,
    "last_update": datetime.now(),
    "message_count": 0,
    "error_count": 0,
    "start_time": datetime.now(),
    "api_calls": 0,
    "failed_apis": 0
}

async def health_check_handler(request):
    """Health check endpoint for UptimeRobot"""
    try:
        uptime_seconds = (datetime.now() - bot_status["start_time"]).total_seconds()
        response_data = {
            "status": "healthy" if bot_status["running"] else "offline",
            "timestamp": datetime.now().isoformat(),
            "uptime_seconds": int(uptime_seconds),
            "message_count": bot_status["message_count"],
            "error_count": bot_status["error_count"],
            "api_calls": bot_status["api_calls"],
            "failed_apis": bot_status["failed_apis"],
            "last_update": bot_status["last_update"].isoformat(),
            "version": "2.0.1-production"
        }
        status_code = 200 if bot_status["running"] else 503
        return web.json_response(response_data, status=status_code)
    except Exception as e:
        logger.error(f"[Health Check Error] {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def stats_handler(request):
    """Statistics endpoint"""
    try:
        uptime_seconds = (datetime.now() - bot_status["start_time"]).total_seconds()
        uptime_hours = uptime_seconds / 3600
        stats = {
            "bot_name": "Beluga",
            "status": "🟢 Online" if bot_status["running"] else "🔴 Offline",
            "uptime": {
                "seconds": int(uptime_seconds),
                "hours": round(uptime_hours, 2),
                "days": round(uptime_hours / 24, 2)
            },
            "messages_processed": bot_status["message_count"],
            "errors_encountered": bot_status["error_count"],
            "api_calls_made": bot_status["api_calls"],
            "failed_api_calls": bot_status["failed_apis"],
            "success_rate": round(
                ((bot_status["api_calls"] - bot_status["failed_apis"]) / max(bot_status["api_calls"], 1)) * 100,
                2
            ),
            "last_activity": bot_status["last_update"].isoformat(),
            "build": "production",
            "version": "2.0.1"
        }
        return web.json_response(stats, status=200)
    except Exception as e:
        logger.error(f"[Stats Error] {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def ping_handler(request):
    """Simple ping endpoint"""
    return web.json_response({"pong": True, "timestamp": datetime.now().isoformat()})

# ==========================================
# PART 1: ENHANCED LOGGING & CONFIG
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BelugaEngine")

# Configuration
DATA_FILE = "beluga_brain.json"
OR_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "5000"))

# Validate token at startup
if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN is missing or invalid!")
    sys.exit(1)

# Global state
db = {}
spam_tracker = {}

def load_db():
    """Load database with error handling"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[DB Load Error] {e}")
    return {"seen": {}, "locks": {}, "counts": {}}

db = load_db()

def save_db():
    """Save database with atomic write"""
    try:
        temp_file = f"{DATA_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(db, f, indent=2)
        if os.path.exists(DATA_FILE):
            os.remove(DATA_FILE)
        os.rename(temp_file, DATA_FILE)
    except Exception as e:
        logger.error(f"[DB Save Error] {e}")
        bot_status["error_count"] += 1

# ==========================================
# PART 2: SAFE ASYNC OPERATIONS
# ==========================================
async def safe_react(bot, chat_id: int, message_id: int, emoji: str = None):
    """Safe reaction with timeout"""
    if not emoji:
        emoji = random.choice(["🐱", "🐾", "❤️", "🔥", "👍", "😻", "😼", "😂", "✨", "👀"])
    try:
        await asyncio.wait_for(
            bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]
            ),
            timeout=5.0
        )
    except:
        pass

# ==========================================
# PART 3: BELUGA PROMPTS
# ==========================================
CHAT_PROMPT = """You are Beluga — a cute, sassy, and incredibly smart 2-year-old cat. NOT a bot, NOT a basic AI, NOT a robot.

=== IDENTITY ===
- Name: Beluga | Built by: Team Oldy Crypto
- Sassy, playful, witty, highly intelligent
- If asked "are you a bot?" → deny playfully: "Bot? No way! I'm a real talking cat! Meow! 🐾"
- Never say "As an AI..."
- Keep responses short (1-2 sentences) and conversational"""

SEARCH_PROMPT = """You are Beluga — the internet-genius cat. Analyze search results and answer perfectly in friendly cat persona. Keep under 150 characters."""

# ==========================================
# PART 4: LANGUAGE DETECTION
# ==========================================
def inject_language_instruction(user_text: str) -> str:
    """Detect and inject language instructions"""
    text_lower = user_text.lower()
    hinglish_tokens = ["kya", "hai", "kaise", "bhai", "batao", "kr", "rha", "tha", "ye", "wo", "tu", "tum", "ko", "nhi", "aur"]
    is_hinglish = any(re.search(rf"\b{word}\b", text_lower) for word in hinglish_tokens)

    if is_hinglish:
        return f"{user_text}\n\n[STRICT: Reply in Hinglish (Roman alphabet only)]"
    elif any(c for c in user_text if '\u0900' <= c <= '\u097F'):
        return f"{user_text}\n\n[STRICT: Reply in Hindi (Devanagari)]"
    else:
        return f"{user_text}\n\n[STRICT: Reply in fluent English]"

# ==========================================
# PART 5: AI ENGINE WITH TIMEOUT
# ==========================================
async def _call_openrouter(system: str, user_text: str) -> Optional[str]:
    """Call OpenRouter with timeout"""
    if not OR_KEY:
        return None
    try:
        loop = asyncio.get_running_loop()
        bot_status["api_calls"] += 1

        def make_request():
            return requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OR_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://t.me/BelugaBot",
                    "X-Title": "BelugaBot"
                },
                json={
                    "model": "meta-llama/llama-3.3-70b-instruct:free",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_text}
                    ],
                    "max_tokens": 256
                },
                timeout=10
            )

        response = await asyncio.wait_for(
            loop.run_in_executor(None, make_request),
            timeout=12.0
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
        return None
    except:
        bot_status["failed_apis"] += 1
        return None

async def _call_groq(system: str, user_text: str) -> Optional[str]:
    """Call Groq with timeout"""
    if not GROQ_KEY:
        return None
    try:
        loop = asyncio.get_running_loop()
        bot_status["api_calls"] += 1

        def make_request():
            return requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_text}
                    ],
                    "max_tokens": 256
                },
                timeout=10
            )

        response = await asyncio.wait_for(
            loop.run_in_executor(None, make_request),
            timeout=12.0
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
        return None
    except:
        bot_status["failed_apis"] += 1
        return None

async def get_ai_response(system: str, user_text: str, fallback: str) -> str:
    """Get AI response with fallbacks"""
    try:
        optimized = inject_language_instruction(user_text)
        reply = await _call_openrouter(system, optimized)
        if reply:
            return reply
        reply = await _call_groq(system, optimized)
        if reply:
            return reply
        return fallback
    except Exception as e:
        logger.error(f"[AI Response] {e}")
        bot_status["error_count"] += 1
        return fallback

async def ask_ai_for_emoji(user_text: str) -> str:
    """Get emoji from AI"""
    try:
        instruction = f"Analyze: '{user_text[:50]}'. Single emoji matching emotion? ONLY emoji."
        res = await _call_groq("You select emojis.", instruction)
        if not res:
            res = await _call_openrouter("You select emojis.", instruction)
        if res:
            emojis = re.findall(r'[^\w\s,.:!?\'\"()\-]+', res)
            if emojis:
                return emojis[0][0]
        return "😼"
    except:
        return "😼"

# ==========================================
# PART 6: WEB SCRAPING
# ==========================================
def scrape_wikipedia(query: str) -> Optional[str]:
    """Scrape Wikipedia"""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query)}&format=json"
        r = requests.get(search_url, headers=headers, timeout=8)

        if r.status_code == 200:
            data = r.json()
            if data.get('query', {}).get('search'):
                page_title = data['query']['search'][0]['title']
                content_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(page_title)}&prop=extracts&explaintext=true&format=json"
                r2 = requests.get(content_url, headers=headers, timeout=8)

                if r2.status_code == 200:
                    pages = r2.json().get('query', {}).get('pages', {})
                    for page_id, page_data in pages.items():
                        extract = page_data.get('extract', '')
                        if extract:
                            summary = extract[:250].strip()
                            return f"📖 **{page_title}**\n\n{summary}..."
        return None
    except Exception as e:
        logger.debug(f"[Wikipedia] {e}")
        return None

def scrape_google(query: str) -> Optional[str]:
    """Scrape Google"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        }
        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=3"
        r = requests.get(url, headers=headers, timeout=8)

        if r.status_code == 200:
            snippets = []
            pattern = r'<span class="VwiC3b">([^<]+)</span>.*?<span class="s">([^<]+)</span>'
            matches = re.findall(pattern, r.text, re.DOTALL)

            for title, desc in matches[:2]:
                clean_title = re.sub('<[^<]+?>', '', title).strip()
                clean_desc = re.sub('<[^<]+?>', '', desc).strip()
                if clean_title and clean_desc:
                    snippets.append(f"📌 {clean_title}\n{clean_desc[:120]}...")

            if snippets:
                return "\n\n".join(snippets)
        return None
    except Exception as e:
        logger.debug(f"[Google] {e}")
        return None

# ==========================================
# PART 7: WEBSITE SCREENSHOT
# ==========================================
async def get_website_screenshot(url: str) -> Optional[str]:
    """Get screenshot"""
    try:
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        services = [
            f"https://image.thum.io/get/width/1280/crop/800/{url}",
            f"https://api.screenshotmachine.com?url={urllib.parse.quote(url)}&dimension=1280x800",
        ]

        loop = asyncio.get_running_loop()

        for service_url in services:
            try:
                def check_url():
                    return requests.head(service_url, timeout=5, allow_redirects=True)

                r = await asyncio.wait_for(
                    loop.run_in_executor(None, check_url),
                    timeout=6.0
                )
                if r.status_code in [200, 301, 302]:
                    return service_url
            except:
                continue
        return None
    except Exception as e:
        logger.debug(f"[Screenshot] {e}")
        return None

# ==========================================
# PART 8: TEMPLATES
# ==========================================
GAY_TEMPLATES = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 **ATTENTION EVERYONE** 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nAfter investigation:\n\n👉 **{u}** 👈\n\nis... 🌈✨ **SUPER GAY** ✨🌈\n\nMust slay forever 💅😭\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📡 **GOVERNMENT ALERT** 📡\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRainbow activity from:\n\n👉 **{u}** 👈\n\n🌈 **Certified Gay Citizen** 🌈\nToo fabulous! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
]

COUPLE_TEMPLATES = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💘 **LOVE DETECTOR 3000** 💘\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nThe perfect couple:\n\n👉 **{u1}** ❤️ **{u2}** 👈\n\nCompatibility: ██████████ 100%\nMade for each other! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 **COUPLE ALERT** 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRomantic activity:\n\n👉 **{u1}** 💞 **{u2}** 👈\n\nToo close! 👀🌚\n\n💖 **OFFICIAL COUPLE** 💖\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handle /gay and /couple"""
    if not u.message:
        return
    try:
        cid = str(u.effective_chat.id)
        cmd = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
        users = list(db.get("seen", {}).get(cid, {}).values())

        if len(users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text("Meow... need more members! 😿🐾")
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

            if "locks" not in db:
                db["locks"] = {}
            db["locks"][lock_key] = {"date": day, "res": res}
            save_db()

        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[Fun Dispatcher] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ==========================================
# PART 9: SEARCH COMMAND
# ==========================================
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handle /search command"""
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text(
                "🐱 **Usage:**\n`/search metaverse` → Wikipedia\n`/search x.com` → Screenshot",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        query = parts[1].strip()
        cid = u.effective_chat.id

        await safe_react(c.bot, cid, u.message.message_id, "🔍")
        await c.bot.send_chat_action(cid, "typing")

        is_url = query.startswith(("http://", "https://", "www.")) or any(
            domain in query for domain in ["x.com", "reddit.com", "github.com", "twitter.com", ".io"]
        )

        if is_url:
            status_msg = await u.message.reply_text("📸 Capturing... 🐾")
            screenshot_url = await get_website_screenshot(query)

            if screenshot_url:
                try:
                    await u.message.reply_photo(
                        photo=screenshot_url,
                        caption=f"🌐 **{query[:40]}**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    await status_msg.delete()
                except:
                    await status_msg.edit_text(f"URL: `{query}`", parse_mode=ParseMode.MARKDOWN)
            else:
                await status_msg.edit_text("⚠️ Screenshot unavailable")
        else:
            status_msg = await u.message.reply_text("🔎 Searching... 🐾")

            loop = asyncio.get_running_loop()
            wiki_result = await loop.run_in_executor(None, scrape_wikipedia, query)

            if not wiki_result:
                google_result = await loop.run_in_executor(None, scrape_google, query)
                result = google_result
            else:
                result = wiki_result

            if result:
                await status_msg.delete()
                await u.message.reply_text(f"🔍 **{query}**\n\n{result}", parse_mode=ParseMode.MARKDOWN)
            else:
                await status_msg.edit_text(f"No results for '{query}'")

        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[Search] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ==========================================
# PART 10: QUIZ COMMAND
# ==========================================
async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handle /quiz"""
    if not u.message:
        return
    try:
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "💡")
        await c.bot.send_chat_action(cid, "typing")

        status_msg = await u.message.reply_text("🎲 Generating... 🐈")
        topics = ["world history", "animals", "pop culture", "astronomy"]
        topic = random.choice(topics)

        quiz_prompt = f"""Generate ONE quiz about '{topic}'.
Format ONLY as JSON:
{{"question": "Q?", "options": ["A", "B", "C", "D"], "correct_index": 0}}"""

        response = await get_ai_response("You generate JSON quizzes.", quiz_prompt, "")
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
                explanation="Beluga knows all! 🐾"
            )
        except:
            await c.bot.send_poll(
                chat_id=cid,
                question="🐱 Which animal has a sandpaper tongue?",
                options=["Dogs", "Cats", "Birds", "Frogs"],
                type="quiz",
                correct_option_id=1,
                is_anonymous=False
            )

        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[Quiz] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ==========================================
# PART 11: MONITOR & AI CHAT
# ==========================================
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Monitor messages"""
    if not u.message or not u.effective_user or u.effective_user.is_bot:
        return
    try:
        uid = u.effective_user.id
        cid = str(u.effective_chat.id)
        now = datetime.now()

        if uid not in spam_tracker:
            spam_tracker[uid] = []
        spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
        spam_tracker[uid].append(now)
        if len(spam_tracker[uid]) >= 4:
            try:
                await u.message.delete()
            except:
                pass
            return

        if cid not in db.get("seen", {}):
            db["seen"] = db.get("seen", {})
            db["seen"][cid] = {}
        db["seen"][cid][str(uid)] = {
            "id": uid,
            "un": u.effective_user.username,
            "n": u.effective_user.first_name
        }

        if "counts" not in db:
            db["counts"] = {}
        db["counts"][cid] = db["counts"].get(cid, 0) + 1
        save_db()

        if db["counts"][cid] % 6 == 0:
            await safe_react(c.bot, cid, u.message.message_id)

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
            try:
                await c.bot.send_chat_action(chat_id=cid, action="typing")
                emoji = await ask_ai_for_emoji(message_text)
                await safe_react(c.bot, cid, u.message.message_id, emoji)

                response = await get_ai_response(CHAT_PROMPT, message_text, "Meow! 🐾")
                await u.message.reply_text(response)
            except Exception as e:
                logger.error(f"[Chat Response] {e}", exc_info=True)

        bot_status["message_count"] += 1
        bot_status["last_update"] = datetime.now()
    except Exception as e:
        logger.error(f"[Monitor] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ==========================================
# PART 12: START COMMAND
# ==========================================
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handle /start"""
    try:
        text = """```
╔══════════════════════════════════════╗
          🐱 BELUGA AI BOT 🐱          
╚══════════════════════════════════════╝
```

💬 **Smart Telegram Chat Bot**

⚡ **Features:**
• AI Chat (mention 'beluga')
• `/search` (Wikipedia + screenshots)
• `/quiz` (Live trivia)
• `/gay` & `/couple` (Fun commands)
• 24/7 Active

👋 *Start chatting now!*"""
        if u.message:
            await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[Start] {e}", exc_info=True)

# ==========================================
# PART 13: ERROR HANDLER
# ==========================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    err = context.error

    if isinstance(err, (NetworkError, TimedOut, RetryAfter)):
        logger.debug(f"[Network] {type(err).__name__}")
        return

    if isinstance(err, (Forbidden, BadRequest)):
        logger.debug(f"[Permission] {type(err).__name__}")
        return

    if isinstance(err, InvalidToken):
        logger.critical("❌ INVALID BOT TOKEN!")
        bot_status["running"] = False
        return

    try:
        tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        logger.error(f"[ERROR]\n{tb}")
        bot_status["error_count"] += 1
    except:
        logger.error(f"[ERROR] {err}")

# ==========================================
# PART 14: MAIN — FIXED EVENT LOOP
# The root cause of the crash was app.run_polling() calling
# loop.run_until_complete() internally, which fails when the
# loop is already running (we called asyncio.run(main())).
#
# Fix: manually initialize/start the PTB app and updater,
# then run the aiohttp server and PTB polling as concurrent
# tasks inside the SAME already-running loop.
# ==========================================
async def main():
    """Main function — aiohttp + PTB polling share one event loop"""
    logger.info("=" * 60)
    logger.info("🐱 BELUGA BOT STARTING v2.0.1")
    logger.info("=" * 60)

    # ── 1. Start aiohttp health-check server ──────────────────
    aio_app = web.Application()
    aio_app.router.add_get('/health', health_check_handler)
    aio_app.router.add_get('/stats',  stats_handler)
    aio_app.router.add_get('/ping',   ping_handler)
    aio_app.router.add_get('/',       ping_handler)

    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HTTP_PORT)
    await site.start()
    logger.info(f"✅ HTTP server on :{HTTP_PORT}  /health /stats /ping")

    # ── 2. Build PTB application ──────────────────────────────
    app = TGApp.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",              start_handler))
    app.add_handler(CommandHandler("search",             search_handler))
    app.add_handler(CommandHandler("quiz",               quiz_handler))
    app.add_handler(CommandHandler(["gay", "couple"],    fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)

    logger.info("✅ Handlers registered")

    # ── 3. Manually initialize & start PTB (no run_polling) ───
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

    bot_status["running"] = True
    logger.info("✅ Bot polling started — running indefinitely")

    # ── 4. Keep alive: sleep forever, wake on KeyboardInterrupt ─
    try:
        while True:
            await asyncio.sleep(3600)   # wake every hour (just to stay alive)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 Shutdown signal received")
    finally:
        # ── 5. Graceful shutdown ───────────────────────────────
        bot_status["running"] = False
        logger.info("🔄 Stopping updater...")
        await app.updater.stop()
        logger.info("🔄 Stopping application...")
        await app.stop()
        await app.shutdown()
        logger.info("🔄 Stopping HTTP server...")
        await runner.cleanup()
        logger.info("✅ Clean shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bye!")
    except InvalidToken:
        logger.critical("❌ FATAL: Invalid BOT_TOKEN")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ FATAL: {e}", exc_info=True)
        sys.exit(1)
