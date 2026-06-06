import os, logging, random, json, asyncio, requests, re
import urllib.parse, traceback, sys, hashlib, time, tempfile, shutil
import base64
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import web
import aiohttp
from bs4 import BeautifulSoup

from telegram import Update, ReactionTypeEmoji, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application as TGApp, CommandHandler, ContextTypes,
    MessageHandler, PollAnswerHandler, CallbackQueryHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest, RetryAfter, InvalidToken

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Beluga")

# ══════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO    = os.environ.get("GITHUB_REPO", "").strip()
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "main").strip()
GITHUB_FILE    = "beluga_db.json"

OR_KEY         = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
HTTP_PORT      = int(os.environ.get("PORT", "10000"))
OWNER_ID       = int(os.environ.get("OWNER_ID", "0"))

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN missing"); sys.exit(1)

bot_status = {
    "running": False, "start_time": datetime.now(),
    "last_update": datetime.now(), "message_count": 0,
    "error_count": 0, "api_calls": 0, "failed_apis": 0,
    "username": ""
}

quiz_cooldown: dict[str, dict[str, float]] = {}
active_polls:  dict[str, dict]             = {}
spam_tracker:  dict[int, list]             = {}
db:            dict                        = {"scores": {}, "weekly": {}}
ttt_games:     dict[str, dict]             = {}
mine_games:    dict[str, dict]             = {}
mine_cooldown: dict[str, dict]             = {}  # Track mine plays per user
user_in_game:  dict[str, str]              = {}
game_timers:   dict[str, dict]             = {}
mine_timers:   dict[str, dict]             = {}  
gm_tracker:    dict[str, tuple]            = {}  
gm_msg_lock:   dict[str, asyncio.Lock]     = {}

GAME_TIMEOUT   = 300
TIMER_DURATION = 60
_dl_tracker:   dict[str, list]             = {}
db_needs_sync  = False
lb_sync_cooldown = 0

LB_IMAGE_URL   = "https://i.postimg.cc/P5THW6RQ/file-00000000bce4720b905dc2e04c58fa80.png"
MINE_IMAGE_URL = "https://i.postimg.cc/hjCftW5b/file-0000000079a071fa95971d3b70015fc0.png"
GM_IMAGE_URL   = "https://i.postimg.cc/mcQhyzFk/image.png"

# ══════════════════════════════════════════════════════
#  ADVANCED GLOBAL ERROR HANDLER
# ══════════════════════════════════════════════════════
class BelugaError(Exception):
    """Custom exception for Beluga bot errors"""
    pass

def format_error_context(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Format error context for logging"""
    try:
        if context.user_data:
            return f"User: {context.user_data}"
        if context.chat_data:
            return f"Chat: {context.chat_data}"
    except:
        pass
    return "No context"

async def advanced_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Advanced error handling with recovery"""
    err = context.error
    err_type = type(err).__name__
    
    if isinstance(err, (NetworkError, TimedOut)):
        logger.debug(f"[Network Error] {err_type}")
        bot_status["error_count"] += 1
        return
    
    if isinstance(err, RetryAfter):
        wait_time = err.retry_after + 1
        logger.warning(f"[Rate Limited] Sleeping {wait_time}s")
        try:
            await asyncio.sleep(wait_time)
            bot_status["error_count"] += 1
        except Exception as e:
            logger.error(f"[Backoff Failed] {e}")
        return
    
    if isinstance(err, (Forbidden, BadRequest)):
        logger.debug(f"[Telegram API] {err_type}")
        bot_status["error_count"] += 1
        return
    
    if isinstance(err, InvalidToken):
        logger.critical("❌ INVALID BOT TOKEN")
        bot_status["running"] = False
        return
    
    logger.error("=" * 70)
    logger.error(f"🔴 UNHANDLED: {err_type}")
    logger.error(f"Context: {format_error_context(context)}")
    logger.error("=" * 70)
    
    if err.__traceback__:
        tb_lines = traceback.format_exception(type(err), err, err.__traceback__)
        for line in tb_lines:
            logger.error(line.rstrip())
    else:
        logger.error(f"Error: {str(err)}")
    
    logger.error("=" * 70)
    bot_status["error_count"] += 1

# ══════════════════════════════════════════════════════
#  GITHUB DB FUNCTIONS
# ══════════════════════════════════════════════════════
def github_load_db():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}?ref={GITHUB_BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            content_b64 = r.json().get("content", "")
            if content_b64:
                content_str = base64.b64decode(content_b64).decode("utf-8")
                data = json.loads(content_str)
                db.update(data)
                logger.info("✅ GitHub DB Loaded")
    except Exception as e:
        logger.error(f"[GitHub Load] {e}")

def github_sync_db():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    sha = None
    try:
        r = requests.get(url + f"?ref={GITHUB_BRANCH}", headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        logger.debug(f"[GitHub SHA] {e}")
        return
    
    try:
        content_str = json.dumps({"scores": db.get("scores", {}), "weekly": db.get("weekly", {})}, indent=2)
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
        payload = {"message": "Auto-sync leaderboard", "content": content_b64, "branch": GITHUB_BRANCH}
        if sha:
            payload["sha"] = sha
        
        r = requests.put(url, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            logger.debug(f"[GitHub Sync] ✅")
    except Exception as e:
        logger.debug(f"[GitHub Sync] {e}")

async def periodic_github_sync():
    global db_needs_sync
    while True:
        await asyncio.sleep(30)
        if db_needs_sync:
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, github_sync_db),
                    timeout=20)
                db_needs_sync = False
            except asyncio.TimeoutError:
                logger.debug("[GitHub Sync] Timeout")
            except Exception as e:
                logger.debug(f"[GitHub Sync] {e}")

def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    global db_needs_sync
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {"name": name, "user_id": int(uid) if uid.lstrip("-").isdigit() else 0, "score": 0})
    e["name"]    = name
    e["user_id"] = int(uid) if uid.lstrip("-").isdigit() else 0
    e["score"]   = max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    db_needs_sync = True
    return e["score"]

def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

# ══════════════════════════════════════════════════════
#  HTTP SERVER
# ══════════════════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({"status": "Healthy", "uptime_seconds": up, "running": bot_status["running"], "messages": bot_status["message_count"], "version": "7.5.0"}, status=200)

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()}, status=200)

async def _stats(req):
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    return web.json_response({"uptime_hours": round(up/3600, 2), "messages": bot_status["message_count"], "errors": bot_status["error_count"]}, status=200)

async def start_http(port: int):
    aio = web.Application()
    aio.router.add_get("/", _ping)
    aio.router.add_get("/ping", _ping)
    aio.router.add_get("/health", _health)
    aio.router.add_get("/stats", _stats)
    runner = web.AppRunner(aio)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ HTTP 0.0.0.0:{port}")
    return runner

# ══════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════
async def safe_react(bot, chat_id: int, msg_id: int, emoji: str = None):
    if not emoji:
        emoji = random.choice(["🐱","🐾","❤️","🔥","👍","😻","😼","😂","✨","👀"])
    try:
        await asyncio.wait_for(
            bot.set_message_reaction(
                chat_id=chat_id, message_id=msg_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]), timeout=5.0)
    except Exception:
        pass

def clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-zA-Z#0-9]+;", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def q_hash(q: str) -> str:
    return hashlib.md5(q.lower().strip().encode()).hexdigest()[:12]

def game_key(msg_id: int, cid: int) -> str:
    return f"{cid}:{msg_id}"

_HINGLISH = ["kya","hai","kaise","bhai","batao","kr","rha","tha","ye","wo","tu","tum","ko","nhi","aur","mujhe","hoga","karo","sab","dost"]

def lang_hint(text: str) -> str:
    lo = text.lower()
    if any(re.search(rf"\b{w}\b", lo) for w in _HINGLISH):
        return text + "\n\n[STRICT: Reply in Hinglish]"
    if any("\u0900" <= c <= "\u097F" for c in text):
        return text + "\n\n[STRICT: Reply in Hindi]"
    return text

# ══════════════════════════════════════════════════════
#  AI ENGINE - FLIRTY & ROASTY PERSONA
# ══════════════════════════════════════════════════════
CHAT_PROMPT = (
    "You are Beluga — a cute, sassy, incredibly smart 2-year-old cat. "
    "NOT a bot, NOT an AI. Built by Team Oldy Crypto. "
    "Deny being a bot playfully. Never say 'As an AI'. "
    "Keep replies short (1-3 sentences), witty and conversational. "
    "IMPORTANT: Sometimes flirt with people in a cute cat way (compliment their energy, vibe, etc). "
    "Also give light roasts when appropriate - be playfully teasing. "
    "Make people smile with your personality. "
    "Keep it fun, never mean-spirited."
)

async def _groq_async(system: str, user: str, max_tok: int = 400) -> Optional[str]:
    if not GROQ_KEY:
        return None
    
    bot_status["api_calls"] += 1
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                "max_tokens": max_tok,
                "temperature": 0.8
            }
            headers = {
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": "application/json"
            }
            
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if text:
                        logger.debug(f"[Groq] ✅")
                        return text
                else:
                    bot_status["failed_apis"] += 1
    except asyncio.TimeoutError:
        logger.debug("[Groq] Timeout")
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[Groq] {e}")
        bot_status["failed_apis"] += 1
    
    return None

async def _or_async(system: str, user: str, max_tok: int = 400) -> Optional[str]:
    if not OR_KEY:
        return None
    
    bot_status["api_calls"] += 1
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "meta-llama/llama-3.3-70b-instruct:free",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                "max_tokens": max_tok,
                "temperature": 0.8
            }
            headers = {
                "Authorization": f"Bearer {OR_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://t.me/BelugaBot",
                "X-Title": "BelugaBot"
            }
            
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if text:
                        logger.debug(f"[OR] ✅")
                        return text
                else:
                    bot_status["failed_apis"] += 1
    except asyncio.TimeoutError:
        logger.debug("[OR] Timeout")
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[OR] {e}")
        bot_status["failed_apis"] += 1
    
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 400) -> str:
    hint = lang_hint(user)
    
    try:
        res = await asyncio.wait_for(_groq_async(system, hint, max_tok), timeout=16)
        if res:
            return res
    except asyncio.TimeoutError:
        logger.debug("[AI] Groq timeout")
    except Exception as e:
        logger.debug(f"[AI] Groq: {e}")
    
    try:
        res = await asyncio.wait_for(_or_async(system, hint, max_tok), timeout=16)
        if res:
            return res
    except asyncio.TimeoutError:
        logger.debug("[AI] OR timeout")
    except Exception as e:
        logger.debug(f"[AI] OR: {e}")
    
    return fallback

async def ai_emoji(text: str) -> str:
    try:
        res = await asyncio.wait_for(
            _groq_async("Output ONE emoji. ONLY emoji.", f"Text: '{text[:60]}'", 10),
            timeout=8)
        if res:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", res)
            if found:
                return found[0][0]
    except Exception:
        pass
    return random.choice(["😼", "🐱", "😂", "🔥"])

def _groq_vision_sync(system: str, image_url: str, prompt: str) -> Optional[str]:
    if not GROQ_KEY:
        return None
    bot_status["api_calls"] += 1
    try:
        payload = {
            "model": "llama-3.2-11b-vision-preview",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]}
            ],
            "max_tokens": 400
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=20)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[Groq Vision] {e}")
        bot_status["failed_apis"] += 1
    return None

# ══════════════════════════════════════════════════════
#  /NEWS - GOOGLE NEWS SCRAPER
# ══════════════════════════════════════════════════════
def fetch_google_news() -> list:
    """Fetch latest news from Google News with proper error handling"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        url = "https://news.google.com/home?hl=en-IN&gl=IN&ceid=IN:en"
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        
        soup = BeautifulSoup(r.content, 'html.parser')
        
        news_items = []
        articles = soup.find_all('article', limit=10)
        
        for article in articles:
            try:
                # Extract headline
                headline_elem = article.find('h3')
                headline = headline_elem.get_text(strip=True) if headline_elem else None
                
                # Extract link
                link_elem = article.find('a', href=True)
                link = link_elem['href'] if link_elem else None
                
                # Extract image
                img_elem = article.find('img', src=True)
                image_url = img_elem['src'] if img_elem else None
                
                # Extract snippet
                snippet_elem = article.find('span', class_=re.compile('snippet'))
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else headline
                
                if headline and link:
                    # Clean up relative URLs
                    if link.startswith('./articles/'):
                        link = 'https://news.google.com' + link[1:]
                    elif link.startswith('/'):
                        link = 'https://news.google.com' + link
                    
                    news_items.append({
                        'headline': headline[:100],
                        'snippet': snippet[:150] if snippet else headline[:150],
                        'image': image_url,
                        'link': link
                    })
            except Exception as e:
                logger.debug(f"[News Parse Item] {e}")
                continue
        
        if news_items:
            logger.info(f"✅ Fetched {len(news_items)} news items")
            return news_items
        else:
            logger.warning("[News] No items found")
            return []
    
    except Exception as e:
        logger.error(f"[Google News Fetch] {e}")
        return []

async def news_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Fetch and display Google News"""
    if not u.message:
        return
    
    try:
        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "📰")
        
        loading_msg = await u.message.reply_text("📰 *Fetching latest news…*", parse_mode=ParseMode.MARKDOWN)
        
        loop = asyncio.get_running_loop()
        news_items = await asyncio.wait_for(
            loop.run_in_executor(None, fetch_google_news),
            timeout=20
        )
        
        try:
            await loading_msg.delete()
        except Exception:
            pass
        
        if not news_items:
            await u.message.reply_text("😿 No news found. Try again later.", parse_mode=ParseMode.MARKDOWN)
            return
        
        for idx, news in enumerate(news_items[:5]):
            try:
                caption = (
                    f"📌 *{news['headline']}*\n\n"
                    f"_{news['snippet']}_\n\n"
                    f"📍 News #{idx + 1}"
                )
                
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔗 KNOW MORE", url=news['link'])
                ]])
                
                if news['image']:
                    try:
                        await u.message.reply_photo(
                            photo=news['image'],
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=keyboard
                        )
                    except Exception as e:
                        logger.debug(f"[News Image] {e}")
                        await u.message.reply_text(
                            caption,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=keyboard
                        )
                else:
                    await u.message.reply_text(
                        caption,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard
                    )
                
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.debug(f"[News Send] {e}")
                continue
        
        bot_status["message_count"] += 1
    
    except asyncio.TimeoutError:
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await u.message.reply_text("⏱️ News fetch timed out. Try again.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[news_handler] {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await u.message.reply_text("❌ Error fetching news.", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════════════
#  /bananalogic - ASK GOOGLE & GET ANSWER
# ══════════════════════════════════════════════════════
def fetch_google_answer(query: str) -> Optional[str]:
    """Fetch answer from Google"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # Try to find answer in knowledge panel
        answer_box = soup.find('div', class_=re.compile('knowledge-panel|card'))
        if answer_box:
            text = answer_box.get_text(strip=True)
            if text:
                return text[:500]
        
        # Try featured snippet
        featured = soup.find('span', class_=re.compile('st'))
        if featured:
            text = featured.get_text(strip=True)
            if text:
                return text[:500]
        
        # Get first search result snippet
        first_result = soup.find('span', class_=re.compile('st'))
        if first_result:
            text = first_result.get_text(strip=True)
            if text:
                return text[:500]
        
        logger.warning(f"[Google Answer] No answer found for: {query}")
        return None
    
    except Exception as e:
        logger.error(f"[Google Answer Fetch] {e}")
        return None

async def bananalogic_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Ask question and get Google answer"""
    if not u.message:
        return
    
    try:
        parts = u.message.text.split(maxsplit=1)
        
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text(
                "🍌 *BananaLogic*\n\n"
                "_Ask me anything and I'll search Google for the answer!_\n\n"
                "Usage: `/bananalogic your question here`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        question = parts[1].strip()
        cid = u.effective_chat.id
        
        await safe_react(c.bot, cid, u.message.message_id, "🧠")
        loading_msg = await u.message.reply_text(
            "🍌 *Searching Google…*",
            parse_mode=ParseMode.MARKDOWN
        )
        
        await c.bot.send_chat_action(cid, "typing")
        
        loop = asyncio.get_running_loop()
        answer = await asyncio.wait_for(
            loop.run_in_executor(None, fetch_google_answer, question),
            timeout=20
        )
        
        try:
            await loading_msg.delete()
        except Exception:
            pass
        
        if answer:
            reply = (
                f"🍌 *Question:* _{question}_\n\n"
                f"📖 *Answer:*\n{answer}"
            )
            await u.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        else:
            await u.message.reply_text(
                f"😿 Couldn't find an answer for: _{question}_",
                parse_mode=ParseMode.MARKDOWN
            )
        
        bot_status["message_count"] += 1
    
    except asyncio.TimeoutError:
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await u.message.reply_text("⏱️ Search timed out. Try again.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[bananalogic] {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await u.message.reply_text("❌ Error processing question.", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════════════
#  /mine GAME - WITH COOLDOWN (20 plays per hour)
# ══════════════════════════════════════════════════════
def check_mine_cooldown(user_id: str) -> tuple[bool, str]:
    """Check if user can play mine. Returns (can_play, message)"""
    now = time.time()
    uid_key = f"mine_cooldown_{user_id}"
    
    if uid_key not in mine_cooldown:
        mine_cooldown[uid_key] = {"plays": 0, "reset_time": now + 3600}
        return (True, "")
    
    cooldown_data = mine_cooldown[uid_key]
    
    # Check if hour has passed
    if now > cooldown_data["reset_time"]:
        cooldown_data["plays"] = 0
        cooldown_data["reset_time"] = now + 3600
        return (True, "")
    
    # Check if reached 20 plays
    if cooldown_data["plays"] >= 20:
        remaining = int(cooldown_data["reset_time"] - now)
        mins = remaining // 60
        secs = remaining % 60
        msg = f"⏳ You've played 20 times! Come back in {mins}m {secs}s"
        return (False, msg)
    
    return (True, "")

async def mine_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    
    try:
        user_id = str(u.effective_user.id)
        can_play, cooldown_msg = check_mine_cooldown(user_id)
        
        if not can_play:
            await u.message.reply_text(f"🚫 {cooldown_msg}", parse_mode=ParseMode.MARKDOWN)
            return
        
        cid = str(u.effective_chat.id)
        gkey = f"{cid}_{user_id}_{int(time.time())}"
        
        mine_games[gkey] = {
            "uid": user_id, "name": u.effective_user.first_name, "bombs": 0, "state": [],
            "revealed": [False]*6, "chat_id": u.effective_chat.id, "msg_id": None, "status": "setting"
        }
        
        msg = await u.message.reply_photo(
            photo=MINE_IMAGE_URL,
            caption="BOOM 🔥 BE CAREFUL !!\n\nChoose number of mines:",
            reply_markup=build_mine_keyboard(gkey, 0, False)
        )
        mine_games[gkey]["msg_id"] = msg.message_id
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[Mine Handler] {e}", exc_info=True)

def build_mine_keyboard(gkey: str, bombs: int, active: bool = False, revealed: bool = False, state: list = None, opened: list = None) -> InlineKeyboardMarkup:
    """Build mine game keyboard"""
    if not active and not revealed:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("3 Mines", callback_data=f"mine:set:{gkey}:3"),
            InlineKeyboardButton("4 Mines", callback_data=f"mine:set:{gkey}:4"),
            InlineKeyboardButton("5 Mines", callback_data=f"mine:set:{gkey}:5"),
        ]])
    
    rows = []
    r = []
    for i in range(6):
        if revealed:
            label = "💣" if state[i] else "✅"
            btn = InlineKeyboardButton(label, callback_data=f"mine:noop:{gkey}:{i}")
        else:
            if opened and opened[i]:
                label = "✅"
                btn = InlineKeyboardButton(label, callback_data=f"mine:noop:{gkey}:{i}")
            else:
                label = "📦"
                btn = InlineKeyboardButton(label, callback_data=f"mine:play:{gkey}:{i}")
        r.append(btn)
        if len(r) == 3:
            rows.append(r)
            r = []
    return InlineKeyboardMarkup(rows)

def mine_build_text(g: dict, rem: int) -> str:
    bombs = g["bombs"]
    opened_count = sum(1 for x in g["revealed"] if x)
    total_safe = 6 - bombs
    
    if g.get("status") == "timeout":
        return f"⏰ *Time Up!*\n\nLost -5 Points."
    elif g.get("status") == "lost":
        return f"BOOM 🔥 BE CAREFUL!!\n\nHit a mine! Lost -5 Points."
    elif g.get("status") == "won":
        return f"🎉 *VICTORY!*\n\nAll {total_safe} safe boxes found! Won +10 Points."
    else:
        return f"BOOM 🔥 BE CAREFUL!!\n\nFind all safe boxes!\n💣 Mines: {bombs}\n📊 Progress: {opened_count}/{total_safe}\n⏱ Time: `{rem}` sec"

async def run_mine_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    """Run mine game timer"""
    try:
        while True:
            await asyncio.sleep(3)
            g = mine_games.get(gkey)
            td = mine_timers.get(gkey)
            
            if not g or not td or g.get("status") != "playing":
                return
            
            td["remaining"] = max(0, td["remaining"] - 3)
            cid = g.get("chat_id")
            msg_id = g.get("msg_id")
            
            if not msg_id:
                return
            
            if td["remaining"] <= 0:
                g["status"] = "timeout"
                cid_s = str(cid)
                uid = g["uid"]
                name = g["name"]
                new_sc = update_score(cid_s, uid, name, -5)
                
                # Increment cooldown counter
                uid_key = f"mine_cooldown_{uid}"
                if uid_key in mine_cooldown:
                    mine_cooldown[uid_key]["plays"] += 1
                
                try:
                    await c.bot.edit_message_caption(
                        chat_id=cid, message_id=msg_id,
                        caption=mine_build_text(g, 0) + f"\n\nBalance: {new_sc:,} Points",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_mine_keyboard(gkey, g["bombs"], active=False, revealed=True, state=g["state"])
                    )
                except Exception:
                    pass
                
                mine_timers.pop(gkey, None)
                mine_games.pop(gkey, None)
                return
            else:
                try:
                    await c.bot.edit_message_caption(
                        chat_id=cid, message_id=msg_id,
                        caption=mine_build_text(g, td["remaining"]),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_mine_keyboard(gkey, g["bombs"], active=True, revealed=False, state=g["state"], opened=g["revealed"])
                    )
                except Exception:
                    pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"[Mine Timer] {e}")

async def mine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle mine game callbacks"""
    q = update.callback_query
    if not q:
        return
    
    try:
        parts = q.data.split(":")
        if len(parts) < 4 or parts[0] != "mine":
            return
        
        action = parts[1]
        gkey = parts[2]
        val = int(parts[3])
        
        if gkey not in mine_games:
            await q.answer("Game expired!", show_alert=True)
            return
        
        g = mine_games[gkey]
        if str(q.from_user.id) != g["uid"]:
            await q.answer("Not your game!", show_alert=True)
            return
        
        if action == "set":
            await q.answer()
            bombs = max(3, min(5, val))
            state = [True] * bombs + [False] * (6 - bombs)
            random.shuffle(state)
            g["bombs"] = bombs
            g["state"] = state
            g["status"] = "playing"
            g["revealed"] = [False] * 6
            
            mine_timers[gkey] = {"remaining": 60}
            asyncio.create_task(run_mine_timer(context, gkey))
            
            await q.edit_message_caption(
                caption=mine_build_text(g, 60),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=build_mine_keyboard(gkey, bombs, active=True, state=state, opened=g["revealed"])
            )
        
        elif action == "play":
            await q.answer()
            state = g["state"]
            is_bomb = state[val]
            cid = str(q.message.chat_id)
            uid = g["uid"]
            name = g["name"]
            
            # Increment cooldown counter
            uid_key = f"mine_cooldown_{uid}"
            if uid_key not in mine_cooldown:
                mine_cooldown[uid_key] = {"plays": 0, "reset_time": time.time() + 3600}
            mine_cooldown[uid_key]["plays"] += 1
            
            if is_bomb:
                g["status"] = "lost"
                new_sc = update_score(cid, uid, name, -5)
                
                await q.edit_message_caption(
                    caption=mine_build_text(g, 0) + f"\n\nBalance: {new_sc:,} Points",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=build_mine_keyboard(gkey, g["bombs"], active=False, revealed=True, state=state)
                )
                mine_timers.pop(gkey, None)
                mine_games.pop(gkey, None)
            else:
                g["revealed"][val] = True
                total_safe = 6 - g["bombs"]
                opened_count = sum(1 for x in g["revealed"] if x)
                
                if gkey in mine_timers:
                    mine_timers[gkey]["remaining"] = 60
                
                if opened_count == total_safe:
                    g["status"] = "won"
                    new_sc = update_score(cid, uid, name, +10)
                    
                    await q.edit_message_caption(
                        caption=mine_build_text(g, 0) + f"\n\nBalance: {new_sc:,} Points",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_mine_keyboard(gkey, g["bombs"], active=False, revealed=True, state=state)
                    )
                    mine_timers.pop(gkey, None)
                    mine_games.pop(gkey, None)
                else:
                    await q.edit_message_caption(
                        caption=mine_build_text(g, mine_timers[gkey]["remaining"]),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_mine_keyboard(gkey, g["bombs"], active=True, revealed=False, state=state, opened=g["revealed"])
                    )
        
        elif action == "noop":
            await q.answer("Already processed!")
    
    except Exception as e:
        logger.error(f"[Mine Callback] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  REMAINING FUNCTIONS (COPY FROM PREVIOUS)
# ══════════════════════════════════════════════════════

WIKI_UA = {"User-Agent": "BelugaBot/7.5"}
G_HDR   = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US"}

def wiki_summary(query: str) -> dict:
    out = {"found": False, "title": "", "url": "", "intro": ""}
    try:
        sr = requests.get("https://en.wikipedia.org/w/api.php",
            params={"action":"query","list":"search","srsearch":query,"srlimit":1,"format":"json"},
            headers=WIKI_UA, timeout=10)
        hits = sr.json().get("query",{}).get("search",[])
        if not hits: return out
        best = hits[0]["title"]
        er = requests.get("https://en.wikipedia.org/w/api.php",
            params={"action":"query","titles":best,"prop":"extracts","explaintext":"true","format":"json"},
            headers=WIKI_UA, timeout=10)
        for pid, page in er.json().get("query",{}).get("pages",{}).items():
            if pid != "-1":
                raw = page.get("extract","").strip()
                if raw:
                    out.update({"found":True,"title":best,"intro":raw[:800]})
                break
    except Exception as e: logger.debug(f"[Wiki] {e}")
    return out

def google_search(query: str) -> dict:
    out = {"found": False, "ai_answer": "", "snippets": []}
    try:
        r = requests.get(f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}",
            headers=G_HDR, timeout=10)
        if r.status_code == 200:
            html = r.text
            for pat in [r'data-attrid="wa:/description"[^>]*>.*?<span[^>]*>([^<]{30,500})']:
                m = re.search(pat, html, re.DOTALL)
                if m:
                    c2 = clean_html(m.group(1))
                    if len(c2) > 30: out["ai_answer"] = c2[:500]; break
            out["found"] = bool(out["ai_answer"])
    except Exception as e: logger.debug(f"[Google] {e}")
    return out

async def ai_summarise(query: str, wiki: dict, goog: dict) -> str:
    ctx = []
    if goog["ai_answer"]: ctx.append(f"Google: {goog['ai_answer']}")
    if wiki["found"]: ctx.append(f"Wikipedia: {wiki['intro']}")
    if not ctx: return ""
    return await ai("Smart assistant. Brief summary max 150 words.",
        f"Query: {query}\n\nData: {chr(10).join(ctx)[:1500]}", "", max_tok=300)

_MEDIA_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_\-]+|(?:youtu\.be|youtube\.com/(?:watch|shorts))[A-Za-z0-9?=&_\-]+)",
    re.IGNORECASE
)

def _dl_rate_ok(cid: str) -> bool:
    now = time.time()
    _dl_tracker.setdefault(cid, [])
    _dl_tracker[cid] = [t for t in _dl_tracker[cid] if now - t < 60]
    if len(_dl_tracker[cid]) >= 3: return False
    _dl_tracker[cid].append(now)
    return True

def _ydl_download(url: str, outdir: str) -> dict:
    result = {"ok": False, "path": None, "type": "video", "title": "", "error": ""}
    try:
        import yt_dlp
    except ImportError:
        return result

    ydl_opts = {
        "format": "best[filesize<45M]",
        "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": 45 * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 5,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info: return result
            
            result["title"] = (info.get("title") or "")[:80].strip()
            
            found = None
            if os.path.exists(outdir):
                files = sorted(os.listdir(outdir), key=lambda x: os.path.getmtime(os.path.join(outdir, x)), reverse=True)
                for f in files:
                    if f.endswith((".mp4", ".webm", ".jpg", ".png")):
                        fp = os.path.join(outdir, f)
                        if os.path.getsize(fp) > 0:
                            found = fp
                            break
            
            if found:
                ext = os.path.splitext(found)[1].lower()
                ftype = "image" if ext in (".jpg", ".png") else "video"
                result.update({"ok": True, "path": found, "type": ftype})
    except Exception as e:
        pass

    return result

async def download_and_send(u: Update, c: ContextTypes.DEFAULT_TYPE, url: str):
    cid = u.effective_chat.id
    if not _dl_rate_ok(str(cid)):
        return

    url_l = url.lower()
    platform = "Instagram" if "instagram" in url_l else "YouTube"
    
    tmpdir = tempfile.mkdtemp(prefix="beluga_dl_")
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _ydl_download, url, tmpdir),
            timeout=90.0)

        if not result["ok"]:
            return

        caption = f"📹 *{platform}*\n_{result['title'][:60]}_" if result["title"] else f"📹 *{platform}*"

        with open(result["path"], "rb") as f:
            if result["type"] == "image":
                await u.message.reply_photo(photo=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
            else:
                await u.message.reply_video(video=f, caption=caption, parse_mode=ParseMode.MARKDOWN, supports_streaming=True)

        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[DL] {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2:
            await u.message.reply_text("🐱 *Usage:* `/search topic`", parse_mode=ParseMode.MARKDOWN); return
        query = parts[1].strip()
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "🔍")
        sm = await u.message.reply_text("🔎 *Searching…*", parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        wiki, goog = await asyncio.gather(
            loop.run_in_executor(None, wiki_summary, query),
            loop.run_in_executor(None, google_search, query))
        summary = await ai_summarise(query, wiki, goog)
        try: await sm.delete()
        except Exception: pass
        if summary:
            await u.message.reply_text(f"🔍 *{query}*\n\n{summary}", parse_mode=ParseMode.MARKDOWN)
        else:
            await u.message.reply_text("😿 No results.", parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[search] {e}")

QUIZ_TOPICS = ["ocean", "space", "history", "science", "animals"]
FALLBACK_QS = [{"q":"What is 2+2?","opts":["3","4","5","6"],"ans":1,"fact":"Math is fun!"}]

def quiz_on_cooldown(cid: str, question: str) -> bool:
    return time.time() < quiz_cooldown.get(cid, {}).get(q_hash(question), 0)

def mark_quiz(cid: str, question: str):
    quiz_cooldown.setdefault(cid, {})[q_hash(question)] = time.time() + 3600

async def gen_quiz(topic: str, cid: str) -> Optional[dict]:
    try:
        raw = await ai("Trivia. ONLY JSON.",
            f"Topic: '{topic}'\n" + '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}',
            "", max_tok=200)
        if raw:
            m = re.search(r"\{[\s\S]+\}", raw)
            if m:
                d = json.loads(m.group(0))
                if d.get("question") and len(d.get("options",[])) == 4:
                    return d
    except Exception:
        pass
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        topic = random.choice(QUIZ_TOPICS)
        cid = str(u.effective_chat.id)
        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "💡")
        sm = await u.message.reply_text("🎲 Generating…")
        qdata = await gen_quiz(topic, cid)
        try: await sm.delete()
        except Exception: pass
        if qdata:
            mark_quiz(cid, qdata["question"])
            try:
                await c.bot.send_poll(chat_id=u.effective_chat.id,
                    question=f"🐱 {qdata['question'][:255]}",
                    options=qdata["options"], type="quiz",
                    correct_option_id=qdata.get("correct_index",0),
                    is_anonymous=False, explanation=qdata.get("fun_fact",""))
            except Exception:
                pass
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[quiz] {e}")

async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        ans = u.poll_answer
        if ans and ans.option_ids:
            cid = str(u.message.chat.id)
            uid = str(ans.user.id)
            update_score(cid, uid, ans.user.first_name or "?", +10)
    except Exception:
        pass

MEDALS = ["🥇","🥈","🥉"]

async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global lb_sync_cooldown
    if not u.message: return
    try:
        cid = str(u.effective_chat.id)
        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)
        lw = db.get("weekly", {}).get(cid, {})

        lines = []
        if lw and lw.get("top3"):
            lines.append("🏆 LAST WEEK 🏆\n")
            for i, e in enumerate(lw["top3"][:3]):
                lines.append(f"{MEDALS[i] if i < 3 else '4.'} {e.get('name','?')} — {e.get('score',0):,} pts")
            lines.append("\n")

        lines.append("🏆 LEADERBOARD 🏆\n")
        if lb:
            for i, e in enumerate(lb[:10]):
                lines.append(f"{MEDALS[i] if i < 3 else f'{i+1}.'} {e.get('name','?')[:15]} — {e.get('score', 0):,} pts")
        else:
            lines.append("No scores yet")

        text = "\n".join(lines)
        try:
            await u.message.reply_photo(photo=LB_IMAGE_URL, caption=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

        now = time.time()
        if now - lb_sync_cooldown > 30:
            lb_sync_cooldown = now
            loop = asyncio.get_running_loop()
            asyncio.create_task(loop.run_in_executor(None, github_sync_db))

        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[lb] {e}")

async def nw_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global db_needs_sync
    if not u.message or not is_owner(u.effective_user.id if u.effective_user else 0):
        return
    try:
        cid = str(u.effective_chat.id)
        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)
        top3 = [{"name": e.get("name","?"), "score": e.get("score",0)} for e in lb[:3]]
        db.setdefault("weekly", {})[cid] = {"top3": top3}
        db["scores"][cid] = {}
        db_needs_sync = True
        await u.message.reply_text("🏆 New week! Scores reset.", parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[nw] {e}")

async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not is_owner(u.effective_user.id if u.effective_user else 0):
        return
    try:
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            return
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            return
        amount = int(parts[1])
        cmd = parts[0].lstrip("/").lower()
        target = u.message.reply_to_message.from_user
        cid = str(u.effective_chat.id)
        new_sc = update_score(cid, str(target.id), (target.first_name or "User")[:30], amount if cmd == "pump" else -amount)
        await u.message.reply_text(f"✅ {target.first_name}: {new_sc:,} pts", parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[pump_dump] {e}")

def _build_gm_caption(users: list, date_str: str) -> str:
    lines = ["📸 DAILY ATTENDANCE\n", f"📅 {date_str} | 👥 {len(users)}\n", "━━━━━━━━━━━━━━━━\n"]
    for i, u in enumerate(users[-10:], 1):
        lines.append(f"{i}. {u['name']} • {u['time']}")
    lines.append("\n🎯 Press GM button to mark!")
    return "\n".join(lines)

def _build_gm_keyboard(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]])

async def gm_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not is_owner(u.effective_user.id if u.effective_user else 0):
        return
    try:
        cid = str(u.effective_chat.id)
        date_str = datetime.now().strftime("%d %b %Y")
        try:
            msg = await u.message.reply_photo(photo=GM_IMAGE_URL, caption=_build_gm_caption([], date_str), reply_markup=_build_gm_keyboard(cid))
        except Exception:
            msg = await u.message.reply_text(_build_gm_caption([], date_str), reply_markup=_build_gm_keyboard(cid))
        gm_tracker[cid] = (msg.message_id, [], date_str)
        gm_msg_lock[cid] = asyncio.Lock()
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[gm] {e}")

async def gm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        parts = q.data.split(":")
        if len(parts) != 3 or parts[0] != "gm": return
        cid = parts[2]
        if cid not in gm_msg_lock:
            gm_msg_lock[cid] = asyncio.Lock()
        async with gm_msg_lock[cid]:
            if cid not in gm_tracker:
                await q.answer("Expired")
                return
            msg_id, users, date_str = gm_tracker[cid]
            user = q.from_user
            if any(uu.get("id") == str(user.id) for uu in users):
                await q.answer("Already marked")
                return
            users.append({"id": str(user.id), "name": user.first_name[:15], "time": datetime.now().strftime("%H:%M")})
            gm_tracker[cid] = (msg_id, users, date_str)
            update_score(str(q.message.chat_id), str(user.id), user.first_name[:15], +50)
            try:
                if q.message.photo:
                    await context.bot.edit_message_caption(chat_id=q.message.chat_id, message_id=msg_id, caption=_build_gm_caption(users, date_str), reply_markup=_build_gm_keyboard(cid))
                else:
                    await context.bot.edit_message_text(chat_id=q.message.chat_id, message_id=msg_id, text=_build_gm_caption(users, date_str), reply_markup=_build_gm_keyboard(cid))
                await q.answer("✅ +50 pts")
            except Exception:
                pass
    except Exception as e: logger.error(f"[gm_callback] {e}")

def register_player(uid: str, gkey: str):
    user_in_game[uid] = gkey

def release_player(uid: str):
    user_in_game.pop(uid, None)

def player_busy(uid: str) -> bool:
    return uid in user_in_game and user_in_game[uid] in ttt_games

async def cleanup_expired_games():
    now = time.time()
    for gkey in list(ttt_games.keys()):
        g = ttt_games[gkey]
        if now - g.get("created", now) > GAME_TIMEOUT:
            for p in [g.get("x_id"), g.get("o_id")]:
                release_player(str(p))
            game_timers.pop(gkey, None)
            del ttt_games[gkey]

TTT_EMPTY = "⬜"; TTT_X = "❌"; TTT_O = "⭕"
WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

def ttt_check_winner(board: list) -> Optional[str]:
    for a,b,c in WINS:
        if board[a] == board[b] == board[c] and board[a] != TTT_EMPTY:
            return board[a]
    return None

def ttt_is_draw(board: list) -> bool:
    return all(c != TTT_EMPTY for c in board) and not ttt_check_winner(board)

def _minimax(board: list, is_max: bool, alpha: int, beta: int) -> int:
    w = ttt_check_winner(board)
    if w == TTT_O: return 10
    if w == TTT_X: return -10
    if all(c != TTT_EMPTY for c in board): return 0
    best = -1000 if is_max else 1000
    for i in range(9):
        if board[i] != TTT_EMPTY: continue
        board[i] = TTT_O if is_max else TTT_X
        score = _minimax(board, not is_max, alpha, beta)
        board[i] = TTT_EMPTY
        best = max(best, score) if is_max else min(best, score)
        alpha = max(alpha, best) if is_max else alpha
        beta = min(beta, best) if not is_max else beta
        if beta <= alpha: break
    return best

def ttt_bot_move(board: list) -> int:
    best_score = -1000
    for i in range(9):
        if board[i] != TTT_EMPTY: continue
        board[i] = TTT_O
        score = _minimax(board, False, -1000, 1000)
        board[i] = TTT_EMPTY
        if score > best_score:
            best_score = score
            best_move = i
    return best_move if best_score > -1000 else -1

def ttt_build_keyboard(board: list, disabled: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx = row*3 + col
            cell = board[idx]
            cb = f"ttt:noop:{idx}" if (cell != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            r.append(InlineKeyboardButton(cell if cell != TTT_EMPTY else TTT_EMPTY, callback_data=cb))
        rows.append(r)
    return InlineKeyboardMarkup(rows)

def ttt_build_text(g: dict) -> str:
    x_name = g["x_name"]; o_name = g["o_name"]
    turn = g["turn"]; status = g.get("status","playing")
    gkey = f"{g['chat_id']}:{g.get('msg_id','')}"
    td = game_timers.get(gkey, {})
    rem = td.get("remaining", 60)
    board = g["board"]
    rows = [" ".join(board[r*3+col] for col in range(3)) for r in range(3)]
    
    if status == "playing":
        sl = f"🎯 *{(x_name if turn=='X' else o_name)}'s Turn*\n⏱ `{rem}s`"
    elif status == "timeout":
        sl = f"⏰ *Time Up!*\n🏆 {g.get('winner_name','')} +10"
    elif status == "draw":
        sl = "🤝 *Draw!*"
    else:
        sl = f"🏆 {g.get('winner_name','')} +10"
    
    return f"🎮 TIC TAC TOE\n━━━━\n{x_name} 🆚 {o_name}\n━━━━\n\n" + "\n".join(rows) + f"\n\n━━━━\n{sl}"

def _ready_keyboard(gkey: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("READY 🔥", callback_data=f"ttt_ready:{gkey}")]])

def _ready_text(g: dict) -> str:
    x_ready = g.get("x_ready", False)
    o_ready = g.get("o_ready", False)
    return f"🎮 WAITING\n{g['x_name']}: {'✅' if x_ready else '⏳'}\n{g['o_name']}: {'✅' if o_ready else '⏳'}"

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games()
        ua = u.effective_user; cid = u.effective_chat.id
        vs_bot = True; user_b_id = None
        
        if u.message.reply_to_message and u.message.reply_to_message.from_user and not u.message.reply_to_message.from_user.is_bot:
            vs_bot = False
            user_b_id = u.message.reply_to_message.from_user.id
            if player_busy(str(user_b_id)):
                await u.message.reply_text("⚠️ Player busy!")
                return
        
        if player_busy(str(ua.id)):
            await u.message.reply_text("⚠️ You're busy!")
            return
        
        board = [TTT_EMPTY] * 9
        g = {
            "board": board, "turn": "X", "x_id": ua.id, "x_name": (ua.first_name or "P1")[:15],
            "o_id": user_b_id, "o_name": (u.message.reply_to_message.from_user.first_name if not vs_bot else "🤖")[:15],
            "vs_bot": vs_bot, "status": "waiting" if not vs_bot else "playing",
            "created": time.time(), "chat_id": cid, "msg_id": None, "x_ready": False, "o_ready": False,
        }
        
        if vs_bot:
            msg = await u.message.reply_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        else:
            gkey_temp = f"{cid}:temp_{int(time.time())}"
            msg = await u.message.reply_text(_ready_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=_ready_keyboard(gkey_temp))
        
        g["msg_id"] = msg.message_id
        gkey = game_key(msg.message_id, cid)
        ttt_games[gkey] = g
        game_timers[gkey] = {"remaining": 60}
        register_player(str(ua.id), gkey)
        if not vs_bot: register_player(str(user_b_id), gkey)
        if vs_bot or not vs_bot:
            asyncio.create_task(run_game_timer(c, gkey))
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[tictac] {e}")

async def run_game_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    try:
        while True:
            await asyncio.sleep(3)
            g = ttt_games.get(gkey)
            td = game_timers.get(gkey)
            if not g or not td or g.get("status") != "playing":
                return
            td["remaining"] = max(0, td["remaining"] - 3)
            if td["remaining"] <= 0:
                loser_uid = str(g["x_id"]) if g["turn"] == "X" else str(g["o_id"])
                loser_name = g["x_name"] if g["turn"] == "X" else g["o_name"]
                winner_name = g["o_name"] if g["turn"] == "X" else g["x_name"]
                g["status"] = "timeout"
                g["winner_name"] = winner_name
                if not g["vs_bot"]:
                    update_score(str(g["chat_id"]), str(g["x_id"] if g["turn"]!="X" else g["o_id"]), winner_name, +10)
                    update_score(str(g["chat_id"]), loser_uid, loser_name, -10)
                try:
                    await c.bot.edit_message_text(chat_id=g["chat_id"], message_id=g["msg_id"],
                        text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"], disabled=True))
                except Exception: pass
                release_player(str(g["x_id"]))
                release_player(str(g["o_id"]))
                game_timers.pop(gkey, None)
                ttt_games.pop(gkey, None)
                return
    except asyncio.CancelledError: pass
    except Exception as e: logger.debug(f"[Timer] {e}")

async def ttt_ready_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        parts = q.data.split(":")
        if len(parts) < 2 or parts[0] != "ttt_ready": return
        cid = q.message.chat_id
        gkey = game_key(q.message.message_id, cid)
        g = ttt_games.get(gkey)
        if not g or g.get("status") != "waiting": return
        uid = str(q.from_user.id)
        if uid == str(g["x_id"]): g["x_ready"] = True
        elif uid == str(g["o_id"]): g["o_ready"] = True
        else: return
        if g["x_ready"] and g["o_ready"]:
            g["status"] = "playing"
            await q.edit_message_text(text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"]))
            asyncio.create_task(run_game_timer(context, gkey))
        else:
            await q.edit_message_text(text=_ready_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=_ready_keyboard(gkey))
    except Exception as e: logger.error(f"[ttt_ready] {e}")

async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        parts = q.data.split(":")
        if len(parts) != 3 or parts[0] != "ttt": return
        action, idx = parts[1], int(parts[2])
        cid, mid = q.message.chat_id, q.message.message_id
        gkey = game_key(mid, cid)
        g = ttt_games.get(gkey)
        if not g or g["status"] != "playing": return
        if action == "noop": return
        uid = str(q.from_user.id)
        if g["turn"] == "X" and uid != str(g["x_id"]): return
        if g["turn"] == "O" and not g["vs_bot"] and uid != str(g["o_id"]): return
        if gkey in game_timers: game_timers[gkey]["remaining"] = 60
        board = g["board"]
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        ws = ttt_check_winner(board)
        if ws or ttt_is_draw(board):
            if ws:
                wname = g["x_name"] if ws == TTT_X else g["o_name"]
                wuid = str(g["x_id"]) if ws == TTT_X else str(g["o_id"])
                lname = g["o_name"] if ws == TTT_X else g["x_name"]
                luid = str(g["o_id"]) if ws == TTT_X else str(g["x_id"])
                g["winner_name"] = wname
                if not g["vs_bot"]:
                    update_score(str(cid), wuid, wname, +10)
                    update_score(str(cid), luid, lname, -10)
                elif ws == TTT_X:
                    update_score(str(cid), wuid, wname, +10)
            g["status"] = "draw" if ttt_is_draw(board) else "win"
            game_timers.pop(gkey, None)
            release_player(str(g["x_id"]))
            release_player(str(g["o_id"]))
            ttt_games.pop(gkey, None)
            await q.edit_message_text(text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            return
        g["turn"] = "O" if g["turn"] == "X" else "X"
        if g["vs_bot"] and g["turn"] == "O":
            bi = ttt_bot_move(board)
            if bi >= 0:
                board[bi] = TTT_O
                if ttt_check_winner(board):
                    g["status"] = "win"
                    g["winner_name"] = g["o_name"]
                    update_score(str(cid), str(g["x_id"]), g["x_name"], -10)
                    game_timers.pop(gkey, None)
                    release_player(str(g["x_id"]))
                    ttt_games.pop(gkey, None)
                    await q.edit_message_text(text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
                    return
                g["turn"] = "X"
        await q.edit_message_text(text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
    except Exception as e: logger.error(f"[ttt_cb] {e}")

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid = str(u.effective_chat.id)
        cmd = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        users = list(db.get("seen",{}).get(cid,{}).values())
        if len(users) < (2 if cmd == "couple" else 1): return
        if cmd == "couple":
            m = random.sample(users, 2)
            res = f"💖 *{m[0]['n']}* 💞 *{m[1]['n']}* 100%"
        else:
            m = [random.choice(users)]
            res = f"🌈 *{m[0]['n']}* IS SUPER GAY! 🌈"
        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[fun] {e}")

async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        text = (
            "✨ 🐱 *BELUGA BOT v7.5* 🐱 ✨\n\n"
            "🎮 `/tictac` ❌🆚⭕ | `/mine` 💣 | `/quiz` 🎲\n"
            "📰 `/news` Latest News | 🍌 `/bananalogic` Ask Google\n"
            "🔍 `/search` Smart Search | 🏆 `/lb` Leaderboard\n"
            "🎉 `/gay` `/couple` Fun | 👑 `/gm` Attendance\n\n"
            "💬 Mention 'beluga' for AI chat!\n"
            "📹 YT/Instagram auto-download"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[start] {e}")

async def photo_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.photo: return
    try:
        caption = (u.message.caption or "").lower()
        if "beluga" not in caption: return
        await c.bot.send_chat_action(u.effective_chat.id, "typing")
        photo_file = await u.message.photo[-1].get_file()
        loop = asyncio.get_running_loop()
        res = await asyncio.wait_for(
            loop.run_in_executor(None, _groq_vision_sync, 
            "Describe this image creatively in 1 sentence.", photo_file.file_path, caption), timeout=25)
        if res:
            await u.message.reply_text(res)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[photo] {e}")

async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    try:
        uid = u.effective_user.id; cid = str(u.effective_chat.id); now = datetime.now()
        spam_tracker.setdefault(uid, [])
        spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
        spam_tracker[uid].append(now)
        if len(spam_tracker[uid]) >= 4:
            try: await u.message.delete()
            except Exception: pass
            return
        db.setdefault("seen",{}).setdefault(cid,{})[str(uid)] = {"id": uid, "un": u.effective_user.username, "n": u.effective_user.first_name or "User"}
        db.setdefault("counts",{})[cid] = db["counts"].get(cid, 0) + 1
        
        text = (u.message.text or u.message.caption or "").strip()
        text_low = text.lower()
        
        media_m = _MEDIA_RE.search(text)
        if media_m:
            asyncio.create_task(download_and_send(u, c, media_m.group(0)))
        
        bot_username = bot_status.get("username", "")
        if text and ("beluga" in text_low or (bot_username and bot_username in text_low) or (u.message.reply_to_message and u.message.reply_to_message.from_user and u.message.reply_to_message.from_user.id == c.bot.id)):
            try:
                await c.bot.send_chat_action(u.effective_chat.id, "typing")
                emoji = await ai_emoji(text)
                await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
                reply = await ai(CHAT_PROMPT, text, "Meow! 🐾")
                await u.message.reply_text(reply)
            except Exception as e: logger.error(f"[monitor/chat] {e}")
        
        bot_status["message_count"] += 1
        bot_status["last_update"] = datetime.now()
    except Exception as e: logger.error(f"[monitor] {e}")

async def main():
    logger.info("🐱 BELUGA BOT v7.5.0 PRODUCTION")
    
    github_load_db()
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)
    
    app = TGApp.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("quiz", quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler("nw", nw_handler))
    app.add_handler(CommandHandler(["gay","couple"], fun_dispatcher))
    app.add_handler(CommandHandler(["pump","dump"], pump_dump_handler))
    app.add_handler(CommandHandler("gm", gm_handler))
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("mine", mine_handler))
    app.add_handler(CommandHandler("news", news_handler))
    app.add_handler(CommandHandler("bananalogic", bananalogic_handler))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(ttt_ready_callback, pattern=r"^ttt_ready:"))
    app.add_handler(CallbackQueryHandler(ttt_callback, pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(gm_callback, pattern=r"^gm:"))
    app.add_handler(CallbackQueryHandler(mine_callback, pattern=r"^mine:"))
    
    # Polls & Messages
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    
    app.add_error_handler(advanced_error_handler)
    
    await app.initialize()
    await app.start()
    
    try:
        me = await app.bot.get_me()
        bot_status["username"] = me.username.lower()
        logger.info(f"🤖 @{me.username}")
    except Exception as e:
        logger.warning(f"[Bot Init] {e}")

    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    bot_status["running"] = True
    logger.info("✅ BELUGA LIVE v7.5.0")
    
    stop_evt = asyncio.Event()
    try:
        import signal
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT, stop_evt.set)
    except:
        pass
    
    cleanup_task = asyncio.create_task(cleanup_expired_games_loop())
    sync_task = asyncio.create_task(periodic_github_sync())
    
    try:
        await stop_evt.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    
    cleanup_task.cancel()
    sync_task.cancel()
    if db_needs_sync:
        github_sync_db()
    
    bot_status["running"] = False
    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try:
            await fn()
        except:
            pass

async def cleanup_expired_games_loop():
    while True:
        await asyncio.sleep(60)
        await cleanup_expired_games()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bye!")
    except Exception as e:
        logger.critical(f"FATAL: {e}")
        sys.exit(1)
