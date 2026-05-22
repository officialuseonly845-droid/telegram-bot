# ═══════════════════════════════════════════════════════════════
#  BELUGA BOT  v3.0.0  —  production-grade, crash-free
#  Fixes: /health+/ping always return 200, full Wikipedia text,
#         quiz no-repeat (1h cooldown per chat), /quiz <topic>,
#         /lb leaderboard, poll_answer handler for scoring
# ═══════════════════════════════════════════════════════════════

import os, logging, random, json, asyncio, requests, re
import urllib.parse, traceback, sys, hashlib, time
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import web

from telegram import Update, ReactionTypeEmoji, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application as TGApp, CommandHandler, ContextTypes,
    MessageHandler, PollAnswerHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import (
    NetworkError, TimedOut, Forbidden, BadRequest,
    RetryAfter, InvalidToken
)

# ══════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Beluga")

# ══════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════
DATA_FILE  = "beluga_brain.json"
OR_KEY     = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY   = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
HTTP_PORT  = int(os.environ.get("PORT", os.environ.get("HTTP_PORT", "5000")))

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN missing or invalid — set env var and redeploy")
    sys.exit(1)

# ══════════════════════════════════════════
#  RUNTIME STATE
# ══════════════════════════════════════════
bot_status = {
    "running": False,
    "start_time": datetime.now(),
    "last_update": datetime.now(),
    "message_count": 0,
    "error_count": 0,
    "api_calls": 0,
    "failed_apis": 0,
}

# quiz state: per-chat
# quiz_cooldown[cid] = {q_hash: expiry_timestamp}
quiz_cooldown: dict[str, dict[str, float]] = {}
# active polls: poll_id → {chat_id, correct_idx, topic, question}
active_polls: dict[str, dict] = {}

spam_tracker: dict[int, list] = {}
db: dict = {}

# ══════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════
def load_db() -> dict:
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[DB Load] {e}")
    return {"seen": {}, "locks": {}, "counts": {}, "scores": {}}

def save_db() -> None:
    try:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(db, f, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        logger.error(f"[DB Save] {e}")
        bot_status["error_count"] += 1

db = load_db()
if "scores" not in db:
    db["scores"] = {}

# ══════════════════════════════════════════
#  HTTP HEALTH SERVER  (always returns 200)
# ══════════════════════════════════════════
async def _health(request):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({
        "status": "healthy",
        "uptime_seconds": up,
        "running": bot_status["running"],
        "messages": bot_status["message_count"],
        "errors": bot_status["error_count"],
        "api_calls": bot_status["api_calls"],
        "version": "3.0.0",
        "timestamp": datetime.now().isoformat(),
    }, status=200)   # ← always 200 so UptimeRobot never fires false alerts

async def _stats(request):
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    return web.json_response({
        "bot": "Beluga",
        "status": "online" if bot_status["running"] else "starting",
        "uptime_hours": round(up / 3600, 2),
        "messages_processed": bot_status["message_count"],
        "errors": bot_status["error_count"],
        "api_calls": bot_status["api_calls"],
        "failed_api_calls": bot_status["failed_apis"],
        "success_rate_pct": round(
            (bot_status["api_calls"] - bot_status["failed_apis"])
            / max(bot_status["api_calls"], 1) * 100, 2
        ),
    }, status=200)

async def _ping(request):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()}, status=200)

async def start_http(port: int):
    app = web.Application()
    app.router.add_get("/",       _ping)
    app.router.add_get("/ping",   _ping)
    app.router.add_get("/health", _health)
    app.router.add_get("/stats",  _stats)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ HTTP server on :{port}  → /health /stats /ping")
    return runner

# ══════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════
async def safe_react(bot, chat_id: int, message_id: int, emoji: str = None):
    if not emoji:
        emoji = random.choice(["🐱","🐾","❤️","🔥","👍","😻","😼","😂","✨","👀"])
    try:
        await asyncio.wait_for(
            bot.set_message_reaction(
                chat_id=chat_id, message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]
            ), timeout=5.0
        )
    except Exception:
        pass

def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def escape_md(text: str) -> str:
    """Escape chars that break Telegram MarkdownV1"""
    # only escape [ ] _ * ` in plain mode
    for ch in ["_", "*", "`", "["]:
        text = text.replace(ch, f"\\{ch}")
    return text

def q_hash(question: str) -> str:
    return hashlib.md5(question.lower().strip().encode()).hexdigest()[:12]

# ══════════════════════════════════════════
#  LANGUAGE DETECTION
# ══════════════════════════════════════════
_HINGLISH = ["kya","hai","kaise","bhai","batao","kr","rha","tha","ye","wo",
             "tu","tum","ko","nhi","aur","mujhe","hoga","karo","sab","dost"]

def language_hint(text: str) -> str:
    lo = text.lower()
    if any(re.search(rf"\b{w}\b", lo) for w in _HINGLISH):
        return f"{text}\n\n[STRICT: Reply in Hinglish Roman script]"
    if any("\u0900" <= c <= "\u097F" for c in text):
        return f"{text}\n\n[STRICT: Reply in Hindi Devanagari]"
    return f"{text}\n\n[STRICT: Reply in fluent English]"

# ══════════════════════════════════════════
#  AI ENGINE
# ══════════════════════════════════════════
CHAT_PROMPT = (
    "You are Beluga — a cute, sassy, incredibly smart 2-year-old cat. "
    "NOT a bot, NOT an AI. Built by Team Oldy Crypto. "
    "If asked 'are you a bot?' deny playfully. Never say 'As an AI'. "
    "Keep replies short (1-3 sentences), witty and conversational."
)

def _openrouter_sync(system: str, user: str, max_tokens: int = 400) -> Optional[str]:
    if not OR_KEY:
        return None
    bot_status["api_calls"] += 1
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OR_KEY}",
                     "Content-Type": "application/json",
                     "HTTP-Referer": "https://t.me/BelugaBot",
                     "X-Title": "BelugaBot"},
            json={"model": "meta-llama/llama-3.3-70b-instruct:free",
                  "messages": [{"role":"system","content":system},
                                {"role":"user","content":user}],
                  "max_tokens": max_tokens},
            timeout=12
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[OR] {e}")
        bot_status["failed_apis"] += 1
    return None

def _groq_sync(system: str, user: str, max_tokens: int = 400) -> Optional[str]:
    if not GROQ_KEY:
        return None
    bot_status["api_calls"] += 1
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role":"system","content":system},
                                {"role":"user","content":user}],
                  "max_tokens": max_tokens},
            timeout=12
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[Groq] {e}")
        bot_status["failed_apis"] += 1
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tokens: int = 400) -> str:
    loop = asyncio.get_running_loop()
    try:
        hint = language_hint(user)
        res = await asyncio.wait_for(
            loop.run_in_executor(None, _groq_sync, system, hint, max_tokens), timeout=14)
        if res:
            return res
        res = await asyncio.wait_for(
            loop.run_in_executor(None, _openrouter_sync, system, hint, max_tokens), timeout=14)
        if res:
            return res
    except Exception as e:
        logger.debug(f"[AI] {e}")
        bot_status["error_count"] += 1
    return fallback

async def ai_emoji(text: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        r = await asyncio.wait_for(
            loop.run_in_executor(None, _groq_sync,
                "You output a single emoji matching the emotion/topic. ONLY the emoji.",
                f"Text: '{text[:60]}'", 10), timeout=6)
        if r:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", r)
            if found:
                return found[0][0]
    except Exception:
        pass
    return "😼"

# ══════════════════════════════════════════
#  WIKIPEDIA — FULL TEXT
# ══════════════════════════════════════════
WIKI_UA = {"User-Agent": "BelugaBot/3.0 (educational Telegram bot)"}

def wiki_fetch(query: str) -> dict:
    """
    Returns full article text split into pages of ≤3800 chars each.
    result = {found, title, url, pages: [str]}
    """
    out = {"found": False, "title": "", "url": "", "pages": []}
    try:
        # 1. Find best page title
        sr = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action":"query","list":"search","srsearch":query,
                    "srlimit":3,"format":"json"},
            headers=WIKI_UA, timeout=10
        )
        hits = sr.json().get("query",{}).get("search",[])
        if not hits:
            return out
        title = hits[0]["title"]

        # 2. Fetch FULL plain-text extract — no exintro, no character limit
        er = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action":"query","titles":title,
                    "prop":"extracts|info","inprop":"url",
                    "explaintext":"true","exsectionformat":"wiki",
                    "format":"json"},
            headers=WIKI_UA, timeout=15
        )
        pages = er.json().get("query",{}).get("pages",{})
        for pid, page in pages.items():
            if pid == "-1":
                continue
            text = page.get("extract","").strip()
            url  = page.get("fullurl",
                f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ','_'))}")
            if not text:
                continue

            # Clean up wiki section markers like == Heading ==
            text = re.sub(r"={2,}\s*(.+?)\s*={2,}", r"\n\n📌 \1\n", text)
            text = re.sub(r"\n{3,}", "\n\n", text)

            # Split into Telegram-safe chunks ≤3800 chars
            chunks = []
            while len(text) > 3800:
                cut = text.rfind("\n\n", 0, 3800)
                if cut == -1:
                    cut = 3800
                chunks.append(text[:cut].strip())
                text = text[cut:].strip()
            if text:
                chunks.append(text)

            out["found"] = True
            out["title"] = title
            out["url"]   = url
            out["pages"] = chunks
            break
    except Exception as e:
        logger.debug(f"[Wiki] {e}")
    return out

# ══════════════════════════════════════════
#  GOOGLE SCRAPE — AI answer + snippets
# ══════════════════════════════════════════
G_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
}

def google_search(query: str) -> dict:
    """
    Scrapes Google for:
      - ai_answer  (SGE / AI overview block)
      - featured   (answer box / featured snippet)
      - snippets   (up to 5 result descriptions)
    """
    out = {"found": False, "ai_answer": "", "featured": "", "snippets": []}
    try:
        r = requests.get(
            f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=6&hl=en",
            headers=G_HEADERS, timeout=10
        )
        if r.status_code != 200:
            return out
        html = r.text

        # AI overview patterns (Google changes class names regularly)
        for pat in [
            r'data-attrid="wa:/description"[^>]*>[\s\S]{0,200}?<span[^>]*>([^<]{40,800})',
            r'class="[^"]*wDYxhc[^"]*"[\s\S]{0,100}?<span[^>]*>([A-Z][^<]{60,600})',
            r'class="[^"]*LGOjhe[^"]*"[^>]*>([^<]{40,600})',
            r'class="[^"]*Zc7IjN[^"]*"[^>]*>([\s\S]{40,600}?)</div',
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c = clean_html(m.group(1))
                if len(c) > 40:
                    out["ai_answer"] = c[:800]
                    break

        # Featured snippet / answer box
        for pat in [
            r'class="[^"]*hgKElc[^"]*"[^>]*>([\s\S]{30,600}?)</span',
            r'class="[^"]*IZ6rdc[^"]*"[^>]*>([\s\S]{30,600}?)</div',
            r'class="[^"]*co8aDb[^"]*"[^>]*>([\s\S]{30,600}?)</div',
            r'data-tts="answers"[^>]*>([\s\S]{20,400}?)</div',
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c = clean_html(m.group(1))
                if len(c) > 30 and c != out["ai_answer"]:
                    out["featured"] = c[:600]
                    break

        # Result snippets
        seen = set()
        for pat in [
            r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,400}?)</div',
            r'class="[^"]*s3v9rd[^"]*"[^>]*>([\s\S]{40,300}?)</span',
            r'class="[^"]*ITZIwc[^"]*"[^>]*>([\s\S]{40,300}?)</span',
        ]:
            for m in re.finditer(pat, html, re.DOTALL):
                t = clean_html(m.group(1))
                if len(t) > 40 and t not in seen:
                    seen.add(t)
                    out["snippets"].append(t[:300])
                if len(out["snippets"]) >= 5:
                    break
            if len(out["snippets"]) >= 5:
                break

        out["found"] = bool(out["ai_answer"] or out["featured"] or out["snippets"])
    except Exception as e:
        logger.debug(f"[Google] {e}")
    return out

def google_quiz_context(topic: str) -> str:
    """Fetch Google snippets about topic to ground quiz questions."""
    try:
        r = requests.get(
            f"https://www.google.com/search?q={urllib.parse.quote_plus(topic+' trivia facts quiz')}&num=4&hl=en",
            headers=G_HEADERS, timeout=8
        )
        if r.status_code != 200:
            return ""
        html = r.text
        bits = []
        for pat in [
            r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,300}?)</div',
            r'class="[^"]*hgKElc[^"]*"[^>]*>([\s\S]{30,300}?)</span',
        ]:
            for m in re.finditer(pat, html, re.DOTALL):
                t = clean_html(m.group(1))
                if len(t) > 30:
                    bits.append(t[:200])
            if len(bits) >= 3:
                break
        return " | ".join(bits[:3])
    except Exception as e:
        logger.debug(f"[QuizCtx] {e}")
        return ""

# ══════════════════════════════════════════
#  SCREENSHOT
# ══════════════════════════════════════════
async def screenshot(url: str) -> Optional[str]:
    if not url.startswith(("http://","https://")):
        url = "https://" + url
    services = [
        f"https://image.thum.io/get/width/1280/crop/800/{url}",
        f"https://mini.s-shot.ru/1280x800/1280/jpeg/?{url}",
    ]
    loop = asyncio.get_running_loop()
    for svc in services:
        try:
            r = await asyncio.wait_for(
                loop.run_in_executor(None,
                    lambda u=svc: requests.head(u, timeout=6, allow_redirects=True)),
                timeout=8)
            if r.status_code in (200, 301, 302):
                return svc
        except Exception:
            continue
    return None

# ══════════════════════════════════════════
#  /search  HANDLER
# ══════════════════════════════════════════
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text(
                "🐱 *Usage:*\n"
                "`/search black holes` → Full Wikipedia + Google AI\n"
                "`/search github.com` → Website screenshot",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        query = parts[1].strip()
        cid   = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "🔍")

        # ── URL → screenshot ──────────────────────────────────
        no_space = " " not in query
        looks_url = no_space and (
            query.startswith(("http://","https://","www.")) or
            re.search(r"\.[a-z]{2,6}(/|$)", query)
        )
        if looks_url:
            sm = await u.message.reply_text("📸 Capturing screenshot… 🐾")
            pic = await screenshot(query)
            if pic:
                try:
                    await u.message.reply_photo(
                        photo=pic,
                        caption=f"🌐 `{query[:60]}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    await sm.delete()
                except Exception:
                    await sm.edit_text(f"🌐 `{query}`\n⚠️ Image failed to load",
                                       parse_mode=ParseMode.MARKDOWN)
            else:
                await sm.edit_text("⚠️ Screenshot service unavailable.")
            bot_status["message_count"] += 1
            return

        # ── Text search ───────────────────────────────────────
        sm = await u.message.reply_text(
            "🔎 *Searching Google + Wikipedia…* 🐾",
            parse_mode=ParseMode.MARKDOWN
        )
        await c.bot.send_chat_action(cid, "typing")

        loop = asyncio.get_running_loop()
        wiki_task   = loop.run_in_executor(None, wiki_fetch, query)
        google_task = loop.run_in_executor(None, google_search, query)
        wiki, goog  = await asyncio.gather(wiki_task, google_task)

        try:
            await sm.delete()
        except Exception:
            pass

        sent_any = False

        # ── Message 1: Google block ───────────────────────────
        g_lines = [f"🔍 *{query}*\n{'━'*32}"]
        if goog["ai_answer"]:
            g_lines.append(f"\n🤖 *Google AI Answer:*\n{goog['ai_answer']}")
        if goog["featured"] and goog["featured"] != goog["ai_answer"]:
            g_lines.append(f"\n⭐ *Featured Snippet:*\n{goog['featured']}")
        if goog["snippets"]:
            snips = "\n\n".join(f"• {s}" for s in goog["snippets"][:4])
            g_lines.append(f"\n🌐 *Web Results:*\n{snips}")

        if goog["found"]:
            try:
                await u.message.reply_text(
                    "\n".join(g_lines),
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
                sent_any = True
            except Exception:
                try:
                    await u.message.reply_text(
                        clean_html("\n".join(g_lines)),
                        disable_web_page_preview=True
                    )
                    sent_any = True
                except Exception as e2:
                    logger.error(f"[Search/Google msg] {e2}")

        # ── Wikipedia: one message per page ──────────────────
        if wiki["found"]:
            header_sent = False
            for i, page_text in enumerate(wiki["pages"]):
                await c.bot.send_chat_action(cid, "typing")
                if i == 0:
                    prefix = (
                        f"📖 *Wikipedia — {wiki['title']}*\n"
                        f"🔗 {wiki['url']}\n{'─'*32}\n\n"
                    )
                    msg = prefix + page_text
                else:
                    msg = f"📄 *{wiki['title']}* _(cont. {i+1}/{len(wiki['pages'])})_\n\n{page_text}"

                try:
                    await u.message.reply_text(
                        msg,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True
                    )
                    sent_any = True
                except Exception:
                    try:
                        await u.message.reply_text(
                            clean_html(msg),
                            disable_web_page_preview=True
                        )
                        sent_any = True
                    except Exception as e2:
                        logger.error(f"[Search/Wiki page {i}] {e2}")

                if i < len(wiki["pages"]) - 1:
                    await asyncio.sleep(0.5)

        if not sent_any:
            await u.message.reply_text(
                f"😿 No results found for *{query}*. Try a different term.",
                parse_mode=ParseMode.MARKDOWN
            )

        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[search_handler] {e}", exc_info=True)
        bot_status["error_count"] += 1
        try:
            await u.message.reply_text("😿 Search failed. Please try again.")
        except Exception:
            pass

# ══════════════════════════════════════════
#  QUIZ — topic pool, no-repeat 1h, /quiz <topic>, leaderboard
# ══════════════════════════════════════════
QUIZ_TOPICS = [
    "deep ocean biology","quantum mechanics","human brain facts",
    "solar system","animal camouflage","photosynthesis","black holes",
    "DNA genetics","volcanoes","climate zones","ancient Egypt",
    "World War 2","Roman Empire","Renaissance","space race 1960s",
    "medieval history","Cold War","ancient Greece","African capitals",
    "South America geography","European mountains","island nations",
    "world deserts","famous inventors","social media history",
    "video game history","Oscar winners Hollywood","internet history",
    "famous paintings art","FIFA World Cup","Olympic Games records",
    "cricket history","NBA basketball history","tennis grand slams",
    "world cuisines food","spices herbs cooking","mythology gods",
    "cryptocurrency blockchain","stock market finance","AI machine learning",
    "famous scientists","famous authors literature","music bands history",
    "space exploration NASA","dinosaurs paleontology","ocean geography",
]

FALLBACK_QS = [
    {"q":"Which planet has the most confirmed moons?",
     "opts":["Jupiter","Saturn","Uranus","Neptune"],"ans":1,
     "fact":"Saturn has 146 confirmed moons as of 2024!"},
    {"q":"What covers approximately 71% of Earth's surface?",
     "opts":["Land","Ice","Water","Atmosphere"],"ans":2,
     "fact":"Oceans cover about 71% of Earth — most of it unexplored!"},
    {"q":"Which country invented paper?",
     "opts":["Egypt","India","China","Greece"],"ans":2,
     "fact":"China invented paper around 105 AD during the Han dynasty."},
    {"q":"How many bones does an adult human body have?",
     "opts":["186","196","206","216"],"ans":2,
     "fact":"Adults have 206 bones. Babies are born with ~270!"},
    {"q":"What is the fastest land animal?",
     "opts":["Lion","Cheetah","Greyhound","Pronghorn"],"ans":1,
     "fact":"Cheetahs can reach 112 km/h in short bursts!"},
    {"q":"Which element has the chemical symbol 'Au'?",
     "opts":["Silver","Aluminium","Gold","Argon"],"ans":2,
     "fact":"Au comes from the Latin word 'aurum' meaning gold."},
    {"q":"How many strings does a standard guitar have?",
     "opts":["4","5","6","7"],"ans":2,
     "fact":"Standard guitars have 6 strings, bass guitars have 4."},
    {"q":"Which ocean is the largest?",
     "opts":["Atlantic","Indian","Arctic","Pacific"],"ans":3,
     "fact":"The Pacific Ocean covers more area than all land combined!"},
    {"q":"In what year did the first iPhone launch?",
     "opts":["2005","2006","2007","2008"],"ans":2,
     "fact":"Steve Jobs revealed the original iPhone on Jan 9, 2007."},
    {"q":"What gas do plants absorb during photosynthesis?",
     "opts":["Oxygen","Nitrogen","CO2","Hydrogen"],"ans":2,
     "fact":"Plants absorb CO₂ and release O₂ — the opposite of us!"},
]

def is_quiz_on_cooldown(cid: str, question: str) -> bool:
    """True if this exact question was asked in the last 1 hour for this chat."""
    now = time.time()
    cooldowns = quiz_cooldown.get(cid, {})
    qh = q_hash(question)
    expiry = cooldowns.get(qh, 0)
    return now < expiry

def mark_quiz_used(cid: str, question: str):
    """Mark question as used for 1 hour in this chat."""
    now = time.time()
    if cid not in quiz_cooldown:
        quiz_cooldown[cid] = {}
    # Prune expired
    quiz_cooldown[cid] = {k: v for k, v in quiz_cooldown[cid].items() if v > now}
    quiz_cooldown[cid][q_hash(question)] = now + 3600  # 1 hour

def _build_quiz_prompt(topic: str, ctx: str) -> tuple[str, str]:
    ctx_line = f"\nReal Google context: {ctx}" if ctx else ""
    system = (
        "You are a trivia quiz master. Generate factual, specific multiple-choice questions. "
        "Output ONLY raw JSON — no markdown fences, no explanation, nothing else."
    )
    user = (
        f"Topic: '{topic}'.{ctx_line}\n\n"
        "Create ONE unique, interesting quiz question about REAL verifiable facts.\n"
        "Rules:\n"
        "- Specific and factual, not generic\n"
        "- 4 distinct answer options\n"
        "- Include a short fun_fact about the correct answer\n"
        "- Do NOT ask 'which animal has a sandpaper tongue'\n\n"
        'Output format (raw JSON only):\n'
        '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}'
    )
    return system, user

async def generate_quiz_question(topic: str, cid: str, max_tries: int = 3) -> Optional[dict]:
    """
    Generate a quiz question for topic, retrying up to max_tries to avoid
    cooldown collisions. Returns dict or None.
    """
    loop = asyncio.get_running_loop()
    for attempt in range(max_tries):
        # Fetch Google context in executor
        try:
            ctx = await asyncio.wait_for(
                loop.run_in_executor(None, google_quiz_context, topic),
                timeout=8)
        except Exception:
            ctx = ""

        system, user_msg = _build_quiz_prompt(topic, ctx)
        raw = await ai(system, user_msg, "", max_tokens=300)
        if not raw:
            continue
        try:
            # Strip markdown fences if any
            cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
            m = re.search(r"\{[\s\S]+\}", cleaned)
            if not m:
                continue
            data = json.loads(m.group(0))
            q    = str(data.get("question","")).strip()
            opts = data.get("options",[])
            idx  = int(data.get("correct_index", 0))
            fact = str(data.get("fun_fact","Beluga knows all! 🐾")).strip()

            if not q or len(opts) != 4 or not (0 <= idx <= 3):
                continue
            if is_quiz_on_cooldown(cid, q):
                continue  # try again with a new question

            return {"question": q, "options": opts, "correct_index": idx, "fun_fact": fact}
        except Exception as e:
            logger.debug(f"[Quiz parse attempt {attempt}] {e}")
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /quiz           → random topic, no repeats within 1h
    /quiz crypto    → Google-sourced crypto quiz
    """
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        cid   = str(u.effective_chat.id)
        cid_i = u.effective_chat.id

        await safe_react(c.bot, cid_i, u.message.message_id, "💡")
        await c.bot.send_chat_action(cid_i, "typing")
        sm = await u.message.reply_text("🎲 Generating quiz… 🐈")

        # If no topic, pick random unused one
        if not topic:
            now = time.time()
            cooldowns = quiz_cooldown.get(cid, {})
            # topics whose most-recent question is off cooldown
            available = [t for t in QUIZ_TOPICS
                         if not any(v > now for v in cooldowns.values()
                                    if False)]  # simplified: just pick random
            topic = random.choice(QUIZ_TOPICS)

        qdata = await generate_quiz_question(topic, cid)

        try:
            await sm.delete()
        except Exception:
            pass

        if qdata:
            mark_quiz_used(cid, qdata["question"])
            try:
                poll_msg = await c.bot.send_poll(
                    chat_id=cid_i,
                    question=f"🐱 {qdata['question'][:255]}",
                    options=[str(o)[:100] for o in qdata["options"]],
                    type="quiz",
                    correct_option_id=qdata["correct_index"],
                    is_anonymous=False,
                    explanation=qdata["fun_fact"][:200]
                )
                # Track poll for leaderboard scoring
                active_polls[poll_msg.poll.id] = {
                    "chat_id": cid_i,
                    "correct_index": qdata["correct_index"],
                    "topic": topic,
                    "question": qdata["question"],
                }
                bot_status["message_count"] += 1
                return
            except Exception as e:
                logger.error(f"[Quiz/send_poll] {e}")

        # Fallback: pick unused fallback question
        now = time.time()
        used_hashes = {h for h, exp in quiz_cooldown.get(cid, {}).items() if exp > now}
        avail_fb = [fb for fb in FALLBACK_QS if q_hash(fb["q"]) not in used_hashes]
        if not avail_fb:
            avail_fb = FALLBACK_QS
        fb = random.choice(avail_fb)
        mark_quiz_used(cid, fb["q"])
        poll_msg = await c.bot.send_poll(
            chat_id=cid_i,
            question=f"🐱 {fb['q']}",
            options=fb["opts"],
            type="quiz",
            correct_option_id=fb["ans"],
            is_anonymous=False,
            explanation=fb["fact"]
        )
        active_polls[poll_msg.poll.id] = {
            "chat_id": cid_i,
            "correct_index": fb["ans"],
            "topic": topic,
            "question": fb["q"],
        }
        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[quiz_handler] {e}", exc_info=True)
        bot_status["error_count"] += 1
        try:
            await u.message.reply_text("😿 Quiz failed — try again!")
        except Exception:
            pass

# ══════════════════════════════════════════
#  POLL ANSWER HANDLER — scores
# ══════════════════════════════════════════
async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Called when a user answers a quiz poll."""
    try:
        ans = u.poll_answer
        if not ans:
            return
        poll_id = ans.poll_id
        info    = active_polls.get(poll_id)
        if not info:
            return

        cid       = str(info["chat_id"])
        user      = ans.user
        uid       = str(user.id)
        name      = (user.first_name or "Unknown")[:30]
        chosen    = ans.option_ids

        if not chosen:
            return  # user retracted

        correct = chosen[0] == info["correct_index"]
        if not correct:
            return

        # +10 points per correct answer
        if "scores" not in db:
            db["scores"] = {}
        if cid not in db["scores"]:
            db["scores"][cid] = {}
        prev = db["scores"][cid].get(uid, {"name": name, "score": 0})
        prev["score"] += 10
        prev["name"]   = name   # update name in case they changed it
        db["scores"][cid][uid] = prev
        save_db()

    except Exception as e:
        logger.debug(f"[poll_answer] {e}")

# ══════════════════════════════════════════
#  /lb  LEADERBOARD
# ══════════════════════════════════════════
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid = str(u.effective_chat.id)
        scores = db.get("scores", {}).get(cid, {})

        if not scores:
            await u.message.reply_text(
                "📊 No scores yet!\nPlay `/quiz` to start earning points 🐾",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Sort descending
        board = sorted(scores.values(), key=lambda x: x["score"], reverse=True)

        lines = [
            "╔════════════════════════════╗",
            "🏆  *QUIZ LEADERBOARD*  🏆",
            "╚════════════════════════════╝\n",
        ]
        for i, entry in enumerate(board[:10]):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
            name  = entry["name"][:20]
            pts   = entry["score"]
            # Pad name for alignment
            lines.append(f"{medal} {name:<20} —  *{pts} pts*")

        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("📈 Sorted: Highest → Lowest")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("_+10 pts per correct answer_")

        await u.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN
        )
        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[lb_handler] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ══════════════════════════════════════════
#  FUN COMMANDS  /gay  /couple
# ══════════════════════════════════════════
GAY_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *ATTENTION EVERYONE* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nAfter deep investigation:\n\n👉 *{u}* 👈\n\nis… 🌈✨ *SUPER GAY* ✨🌈\n\nMust slay forever 💅😭\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📡 *GOVERNMENT ALERT* 📡\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRainbow activity detected from:\n\n👉 *{u}* 👈\n\n🌈 *Certified Gay Citizen* 🌈\nToo fabulous! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
]
COUPLE_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💘 *LOVE DETECTOR 3000* 💘\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nThe perfect couple:\n\n👉 *{u1}* ❤️ *{u2}* 👈\n\nCompatibility: ██████████ 100%\nMade for each other! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *COUPLE ALERT* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRomantic activity:\n\n👉 *{u1}* 💞 *{u2}* 👈\n\nToo close! 👀🌚\n\n💖 *OFFICIAL COUPLE* 💖\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid  = str(u.effective_chat.id)
        cmd  = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        users = list(db.get("seen",{}).get(cid,{}).values())

        if len(users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text("Meow… need more chat members! 😿🐾")
            return

        day      = datetime.now().strftime("%y-%m-%d")
        lock_key = f"{cid}:{cmd}"

        if lock_key in db.get("locks",{}) and db["locks"][lock_key]["date"] == day:
            res = db["locks"][lock_key]["res"]
        else:
            if cmd == "couple":
                m   = random.sample(users, 2)
                res = random.choice(COUPLE_T).format(u1=m[0]["n"], u2=m[1]["n"])
            else:
                m   = [random.choice(users)]
                res = random.choice(GAY_T).format(u=m[0]["n"])
            db.setdefault("locks",{})[lock_key] = {"date": day, "res": res}
            save_db()

        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[fun_dispatcher] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ══════════════════════════════════════════
#  /start
# ══════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        text = (
            "```\n"
            "╔══════════════════════════════════════╗\n"
            "         🐱  BELUGA AI BOT  🐱         \n"
            "╚══════════════════════════════════════╝\n"
            "```\n\n"
            "💬 *Smart Telegram Chat Bot v3*\n\n"
            "⚡ *Commands:*\n"
            "• `/search <topic>` — Full Wikipedia + Google AI\n"
            "• `/search <url>` — Website screenshot\n"
            "• `/quiz` — Random trivia (no repeats 1h)\n"
            "• `/quiz crypto` — Topic-specific quiz\n"
            "• `/lb` — Quiz leaderboard 🏆\n"
            "• `/gay` `/couple` — Fun daily commands\n"
            "• Mention *beluga* — AI chat 🐾\n\n"
            "👋 _Start chatting now!_"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[start_handler] {e}", exc_info=True)

# ══════════════════════════════════════════
#  MESSAGE MONITOR (track users + AI chat)
# ══════════════════════════════════════════
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot:
        return
    try:
        uid  = u.effective_user.id
        cid  = str(u.effective_chat.id)
        now  = datetime.now()

        # Spam guard: 4 msgs in 2 seconds → delete + ignore
        spam_tracker.setdefault(uid, [])
        spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
        spam_tracker[uid].append(now)
        if len(spam_tracker[uid]) >= 4:
            try:
                await u.message.delete()
            except Exception:
                pass
            return

        # Track user
        db.setdefault("seen", {}).setdefault(cid, {})[str(uid)] = {
            "id": uid,
            "un": u.effective_user.username,
            "n":  u.effective_user.first_name or "User",
        }
        db.setdefault("counts",{})[cid] = db["counts"].get(cid, 0) + 1
        save_db()

        # Periodic reaction
        if db["counts"][cid] % 6 == 0:
            await safe_react(c.bot, u.effective_chat.id, u.message.message_id)

        text     = (u.message.text or "").strip()
        text_low = text.lower()

        beluga_named  = "beluga" in text_low
        reply_to_bot  = (
            u.message.reply_to_message and
            u.message.reply_to_message.from_user and
            u.message.reply_to_message.from_user.id == c.bot.id
        )
        mention_bot = False
        for ent in (u.message.entities or []):
            if ent.type == "mention" and "beluga" in text_low[ent.offset:ent.offset+ent.length]:
                mention_bot = True
                break

        if beluga_named or reply_to_bot or mention_bot:
            try:
                await c.bot.send_chat_action(u.effective_chat.id, "typing")
                emoji = await ai_emoji(text)
                await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
                reply = await ai(CHAT_PROMPT, text, "Meow! 🐾")
                await u.message.reply_text(reply)
            except Exception as e:
                logger.error(f"[monitor/chat] {e}", exc_info=True)

        bot_status["message_count"] += 1
        bot_status["last_update"] = datetime.now()
    except Exception as e:
        logger.error(f"[monitor] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ══════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.debug(f"[Network] {type(err).__name__}: {err}")
        return
    if isinstance(err, RetryAfter):
        logger.warning(f"[RateLimit] retry after {err.retry_after}s")
        await asyncio.sleep(err.retry_after + 1)
        return
    if isinstance(err, Forbidden):
        logger.debug(f"[Forbidden] bot kicked or blocked")
        return
    if isinstance(err, BadRequest):
        logger.debug(f"[BadRequest] {err}")
        return
    if isinstance(err, InvalidToken):
        logger.critical("❌ BOT TOKEN REJECTED — revoke and regenerate in @BotFather")
        bot_status["running"] = False
        return
    # Unknown error — log full traceback
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logger.error(f"[UnhandledError]\n{tb}")
    bot_status["error_count"] += 1

# ══════════════════════════════════════════
#  MAIN  — single event loop, no run_polling
# ══════════════════════════════════════════
async def main():
    logger.info("=" * 55)
    logger.info("🐱  BELUGA BOT  v3.0.0  starting…")
    logger.info(f"   HTTP port : {HTTP_PORT}")
    logger.info("=" * 55)

    # STEP 1: HTTP server FIRST — must bind port before anything
    # else so Render health check passes within deploy timeout.
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.5)  # give aiohttp a tick to accept conns
    logger.info(f"✅ HTTP server ready on :{HTTP_PORT}")

    # STEP 2: Build PTB app
    app = TGApp.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",              start_handler))
    app.add_handler(CommandHandler("search",             search_handler))
    app.add_handler(CommandHandler("quiz",               quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler(["gay","couple"],     fun_dispatcher))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)
    logger.info("✅ Handlers registered")

    # STEP 3: Init PTB — network call to Telegram happens AFTER
    # HTTP is already serving, so Render health check won't time out.
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    bot_status["running"] = True
    logger.info("✅ Telegram polling started — bot is fully live 🐱")

    # STEP 4: Keep alive — signal-aware stop event
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        import signal
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)
        loop.add_signal_handler(signal.SIGINT,  stop_event.set)
    except (NotImplementedError, RuntimeError):
        pass  # Windows fallback

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    # STEP 5: Graceful shutdown
    bot_status["running"] = False
    logger.info("🔄 Shutting down gracefully…")
    for coro in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try:
            await coro()
        except Exception as ex:
            logger.debug(f"[Shutdown] {ex}")
    logger.info("✅ Shutdown complete")

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
