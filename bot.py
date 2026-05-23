# ═══════════════════════════════════════════════════════════════
#  BELUGA BOT  v4.0.0
#  - /health + /ping always 200, HTTP binds FIRST
#  - /search: AI summary only (no spam), correct Wikipedia match
#  - /quiz: no repeats 1h, /quiz <topic>, leaderboard scoring
#  - /pump /dump: owner-only point manipulation (reply to user)
#  - GitHub persistence: scores saved to GitHub Gist
#  - Memory: user IDs + points stored in GitHub
# ═══════════════════════════════════════════════════════════════

import os, logging, random, json, asyncio, requests, re
import urllib.parse, traceback, sys, hashlib, time, base64
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import web

from telegram import Update, ReactionTypeEmoji
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
#  CONFIG  (all from env vars)
# ══════════════════════════════════════════
DATA_FILE    = "beluga_brain.json"
OR_KEY       = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY     = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
HTTP_PORT    = int(os.environ.get("PORT", "10000"))
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))       # your Telegram user ID
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")          # GitHub personal access token
GITHUB_GIST  = os.environ.get("GITHUB_GIST_ID", "")       # Gist ID for persistence

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN missing — set env var and redeploy")
    sys.exit(1)

# ══════════════════════════════════════════
#  RUNTIME STATE
# ══════════════════════════════════════════
bot_status = {
    "running":       False,
    "start_time":    datetime.now(),
    "last_update":   datetime.now(),
    "message_count": 0,
    "error_count":   0,
    "api_calls":     0,
    "failed_apis":   0,
}

quiz_cooldown: dict[str, dict[str, float]] = {}   # cid → {q_hash: expiry}
active_polls:  dict[str, dict]             = {}   # poll_id → info
spam_tracker:  dict[int, list]             = {}
db:            dict                        = {}

# ══════════════════════════════════════════
#  LOCAL DATABASE
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
db.setdefault("scores", {})

# ══════════════════════════════════════════
#  GITHUB GIST PERSISTENCE
#  Saves scores to a GitHub Gist so data
#  survives Render redeploys (ephemeral disk)
# ══════════════════════════════════════════
GIST_FILENAME = "beluga_scores.json"

def github_load_scores() -> dict:
    """Load scores from GitHub Gist on startup."""
    if not GITHUB_TOKEN or not GITHUB_GIST:
        logger.info("[GitHub] No token/gist configured — using local storage only")
        return {}
    try:
        r = requests.get(
            f"https://api.github.com/gists/{GITHUB_GIST}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=10
        )
        if r.status_code == 200:
            files = r.json().get("files", {})
            if GIST_FILENAME in files:
                content = files[GIST_FILENAME].get("content", "{}")
                scores = json.loads(content)
                logger.info(f"[GitHub] Loaded scores from Gist ({len(scores)} chats)")
                return scores
        logger.warning(f"[GitHub] Gist load failed: {r.status_code}")
    except Exception as e:
        logger.error(f"[GitHub Load] {e}")
    return {}

def github_save_scores() -> bool:
    """Save scores to GitHub Gist (non-blocking, called after score changes)."""
    if not GITHUB_TOKEN or not GITHUB_GIST:
        return False
    try:
        scores = db.get("scores", {})
        r = requests.patch(
            f"https://api.github.com/gists/{GITHUB_GIST}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={
                "files": {
                    GIST_FILENAME: {
                        "content": json.dumps(scores, indent=2)
                    }
                }
            },
            timeout=10
        )
        if r.status_code == 200:
            logger.info(f"[GitHub] Scores saved to Gist ✅")
            return True
        logger.warning(f"[GitHub] Gist save failed: {r.status_code}")
    except Exception as e:
        logger.error(f"[GitHub Save] {e}")
    return False

async def async_github_save():
    """Run github_save_scores in executor so it doesn't block the event loop."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, github_save_scores)
    except Exception as e:
        logger.debug(f"[GitHub Async Save] {e}")

def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    """Add delta (positive or negative) to user's score. Returns new score."""
    db.setdefault("scores", {}).setdefault(cid, {})
    entry = db["scores"][cid].get(uid, {"name": name, "score": 0})
    entry["name"]  = name
    entry["score"] = max(0, entry["score"] + delta)   # floor at 0
    db["scores"][cid][uid] = entry
    save_db()
    return entry["score"]

# ══════════════════════════════════════════
#  HTTP SERVER  — always 200
# ══════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({
        "status":         "healthy",
        "uptime_seconds": up,
        "running":        bot_status["running"],
        "messages":       bot_status["message_count"],
        "errors":         bot_status["error_count"],
        "api_calls":      bot_status["api_calls"],
        "version":        "4.0.0",
        "ts":             datetime.now().isoformat(),
    }, status=200)

async def _stats(req):
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    ok = bot_status["api_calls"] - bot_status["failed_apis"]
    return web.json_response({
        "bot":              "Beluga",
        "uptime_hours":     round(up / 3600, 2),
        "messages":         bot_status["message_count"],
        "errors":           bot_status["error_count"],
        "api_calls":        bot_status["api_calls"],
        "failed_api_calls": bot_status["failed_apis"],
        "success_rate_pct": round(ok / max(bot_status["api_calls"], 1) * 100, 2),
    }, status=200)

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()}, status=200)

async def start_http(port: int):
    aio = web.Application()
    aio.router.add_get("/",       _ping)
    aio.router.add_get("/ping",   _ping)
    aio.router.add_get("/health", _health)
    aio.router.add_get("/stats",  _stats)
    runner = web.AppRunner(aio)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ HTTP  0.0.0.0:{port}  /health /ping /stats")
    return runner

# ══════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════
async def safe_react(bot, chat_id: int, msg_id: int, emoji: str = None):
    if not emoji:
        emoji = random.choice(["🐱","🐾","❤️","🔥","👍","😻","😼","😂","✨","👀"])
    try:
        await asyncio.wait_for(
            bot.set_message_reaction(
                chat_id=chat_id, message_id=msg_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]
            ), timeout=5.0)
    except Exception:
        pass

def clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-zA-Z#0-9]+;", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def q_hash(q: str) -> str:
    return hashlib.md5(q.lower().strip().encode()).hexdigest()[:12]

def is_owner(user_id: int) -> bool:
    return OWNER_ID != 0 and user_id == OWNER_ID

# ══════════════════════════════════════════
#  LANGUAGE DETECTION
# ══════════════════════════════════════════
_HINGLISH = ["kya","hai","kaise","bhai","batao","kr","rha","tha","ye","wo",
             "tu","tum","ko","nhi","aur","mujhe","hoga","karo","sab","dost"]

def lang_hint(text: str) -> str:
    lo = text.lower()
    if any(re.search(rf"\b{w}\b", lo) for w in _HINGLISH):
        return text + "\n\n[STRICT: Reply in Hinglish Roman script]"
    if any("\u0900" <= c <= "\u097F" for c in text):
        return text + "\n\n[STRICT: Reply in Hindi Devanagari]"
    return text + "\n\n[STRICT: Reply in fluent English]"

# ══════════════════════════════════════════
#  AI ENGINE
# ══════════════════════════════════════════
CHAT_PROMPT = (
    "You are Beluga — a cute, sassy, incredibly smart 2-year-old cat. "
    "NOT a bot, NOT an AI. Built by Team Oldy Crypto. "
    "Deny being a bot playfully. Never say 'As an AI'. "
    "Keep replies short (1-3 sentences), witty and conversational."
)

def _groq_sync(system: str, user: str, max_tok: int = 400) -> Optional[str]:
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
                  "max_tokens": max_tok},
            timeout=12
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[Groq] {e}")
        bot_status["failed_apis"] += 1
    return None

def _or_sync(system: str, user: str, max_tok: int = 400) -> Optional[str]:
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
                  "max_tokens": max_tok},
            timeout=12
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[OR] {e}")
        bot_status["failed_apis"] += 1
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 400) -> str:
    loop = asyncio.get_running_loop()
    try:
        hint = lang_hint(user)
        for fn in [_groq_sync, _or_sync]:
            try:
                res = await asyncio.wait_for(
                    loop.run_in_executor(None, fn, system, hint, max_tok), timeout=14)
                if res:
                    return res
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[AI] {e}")
        bot_status["error_count"] += 1
    return fallback

async def ai_emoji(text: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        r = await asyncio.wait_for(
            loop.run_in_executor(None, _groq_sync,
                "Output ONE emoji matching the emotion. ONLY the emoji, nothing else.",
                f"Text: '{text[:60]}'", 10), timeout=6)
        if r:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", r)
            if found:
                return found[0][0]
    except Exception:
        pass
    return "😼"

# ══════════════════════════════════════════
#  WIKIPEDIA — smart match + AI summary
# ══════════════════════════════════════════
WIKI_UA = {"User-Agent": "BelugaBot/4.0 (educational Telegram bot)"}

def wiki_summary(query: str) -> dict:
    """
    Finds the BEST matching Wikipedia article for the query,
    returns a concise intro + section list.
    No spam — just the right article, summarised.
    result = {found, title, url, intro, sections}
    """
    out = {"found": False, "title": "", "url": "", "intro": "", "sections": []}
    try:
        # 1. Search — get top 5 candidates
        sr = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action":"query","list":"search","srsearch":query,
                    "srlimit":5,"format":"json"},
            headers=WIKI_UA, timeout=10
        )
        hits = sr.json().get("query",{}).get("search",[])
        if not hits:
            return out

        # 2. Pick the most relevant title (first hit is usually best)
        # Boost exact title match
        query_lower = query.lower().strip()
        best_title = hits[0]["title"]
        for h in hits:
            if h["title"].lower() == query_lower:
                best_title = h["title"]
                break

        # 3. Fetch full extract
        er = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action":"query","titles":best_title,
                    "prop":"extracts|info","inprop":"url",
                    "explaintext":"true","exsectionformat":"wiki",
                    "format":"json"},
            headers=WIKI_UA, timeout=15
        )
        pages = er.json().get("query",{}).get("pages",{})
        for pid, page in pages.items():
            if pid == "-1":
                continue
            raw  = page.get("extract","").strip()
            url  = page.get("fullurl",
                f"https://en.wikipedia.org/wiki/{urllib.parse.quote(best_title.replace(' ','_'))}")
            if not raw:
                continue

            # Split into intro vs sections
            # Sections are denoted by == Heading == in wiki format
            parts = re.split(r"\n(==+)\s*(.+?)\s*\1\n", raw)
            intro = parts[0].strip()

            # Collect sections
            sections = []
            i = 1
            while i + 2 < len(parts):
                sec_title = parts[i + 1].strip()
                sec_body  = parts[i + 2].strip() if i + 2 < len(parts) else ""
                if sec_body and sec_title not in (
                    "See also","References","Further reading",
                    "External links","Notes","Bibliography","Citations"
                ):
                    sections.append({"h": sec_title, "b": sec_body[:800]})
                i += 3

            out.update({
                "found":    True,
                "title":    best_title,
                "url":      url,
                "intro":    intro[:1200],
                "sections": sections[:8],   # max 8 sections
            })
            break
    except Exception as e:
        logger.debug(f"[Wiki] {e}")
    return out

# ══════════════════════════════════════════
#  GOOGLE — AI answer + featured snippet
# ══════════════════════════════════════════
G_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
}

def google_search(query: str) -> dict:
    out = {"found": False, "ai_answer": "", "featured": "", "snippets": []}
    try:
        r = requests.get(
            f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=5&hl=en",
            headers=G_HDR, timeout=10)
        if r.status_code != 200:
            return out
        html = r.text

        # AI overview / SGE answer
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

        # Featured snippet
        for pat in [
            r'class="[^"]*hgKElc[^"]*"[^>]*>([\s\S]{30,500}?)</span',
            r'class="[^"]*IZ6rdc[^"]*"[^>]*>([\s\S]{30,500}?)</div',
            r'class="[^"]*co8aDb[^"]*"[^>]*>([\s\S]{30,500}?)</div',
            r'data-tts="answers"[^>]*>([\s\S]{20,400}?)</div',
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c = clean_html(m.group(1))
                if len(c) > 30 and c != out["ai_answer"]:
                    out["featured"] = c[:500]
                    break

        # Up to 3 snippets only (no spam)
        seen = set()
        for pat in [
            r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,350}?)</div',
            r'class="[^"]*s3v9rd[^"]*"[^>]*>([\s\S]{40,300}?)</span',
        ]:
            for m in re.finditer(pat, html, re.DOTALL):
                t = clean_html(m.group(1))
                if len(t) > 40 and t not in seen:
                    seen.add(t)
                    out["snippets"].append(t[:250])
                if len(out["snippets"]) >= 3:
                    break
            if len(out["snippets"]) >= 3:
                break

        out["found"] = bool(out["ai_answer"] or out["featured"] or out["snippets"])
    except Exception as e:
        logger.debug(f"[Google] {e}")
    return out

def google_quiz_ctx(topic: str) -> str:
    try:
        r = requests.get(
            f"https://www.google.com/search?q={urllib.parse.quote_plus(topic+' trivia facts')}&num=3&hl=en",
            headers=G_HDR, timeout=8)
        if r.status_code != 200:
            return ""
        bits = []
        for pat in [
            r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,250}?)</div',
            r'class="[^"]*hgKElc[^"]*"[^>]*>([\s\S]{30,250}?)</span',
        ]:
            for m in re.finditer(pat, r.text, re.DOTALL):
                t = clean_html(m.group(1))
                if len(t) > 30:
                    bits.append(t[:180])
            if len(bits) >= 3:
                break
        return " | ".join(bits[:3])
    except Exception:
        return ""

# ══════════════════════════════════════════
#  AI SUMMARISE for /search
# ══════════════════════════════════════════
async def ai_summarise(query: str, wiki: dict, goog: dict) -> str:
    """
    Ask the AI to write a clean, focused summary from wiki + google data.
    Returns a single well-formatted message string.
    """
    # Build context for the AI
    ctx_parts = []
    if goog["ai_answer"]:
        ctx_parts.append(f"Google AI Answer: {goog['ai_answer']}")
    if goog["featured"]:
        ctx_parts.append(f"Featured: {goog['featured']}")
    if goog["snippets"]:
        ctx_parts.append("Web snippets:\n" + "\n".join(f"- {s}" for s in goog["snippets"]))
    if wiki["found"]:
        ctx_parts.append(f"Wikipedia ({wiki['title']}):\n{wiki['intro']}")
        for sec in wiki["sections"][:4]:
            ctx_parts.append(f"[{sec['h']}] {sec['b']}")

    if not ctx_parts:
        return ""

    context = "\n\n".join(ctx_parts)[:3000]

    system = (
        "You are a smart assistant. Given research data, write a clean, accurate, "
        "well-structured summary about the query. Use bullet points for key facts. "
        "Be concise but complete. Max 350 words. Use simple Telegram markdown: "
        "*bold* for headers, - for bullets. Do NOT include links."
    )
    user = f"Query: {query}\n\nResearch data:\n{context}\n\nWrite a focused summary:"

    return await ai(system, user, "", max_tok=500)

# ══════════════════════════════════════════
#  SCREENSHOT
# ══════════════════════════════════════════
async def screenshot(url: str) -> Optional[str]:
    if not url.startswith(("http://","https://")):
        url = "https://" + url
    svcs = [
        f"https://image.thum.io/get/width/1280/crop/800/{url}",
        f"https://mini.s-shot.ru/1280x800/1280/jpeg/?{url}",
    ]
    loop = asyncio.get_running_loop()
    for svc in svcs:
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
#  /search HANDLER  (smart, no spam)
# ══════════════════════════════════════════
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text(
                "🐱 *Usage:*\n"
                "`/search Michael Jackson` → Smart summary\n"
                "`/search github.com` → Website screenshot",
                parse_mode=ParseMode.MARKDOWN)
            return

        query = parts[1].strip()
        cid   = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "🔍")

        # URL → screenshot mode
        no_space = " " not in query
        looks_url = no_space and (
            query.startswith(("http://","https://","www.")) or
            re.search(r"\.[a-z]{2,6}(/|$)", query)
        )
        if looks_url:
            sm = await u.message.reply_text("📸 Capturing… 🐾")
            pic = await screenshot(query)
            if pic:
                try:
                    await u.message.reply_photo(
                        photo=pic,
                        caption=f"🌐 `{query[:60]}`",
                        parse_mode=ParseMode.MARKDOWN)
                    await sm.delete()
                except Exception:
                    await sm.edit_text(f"🌐 `{query}`", parse_mode=ParseMode.MARKDOWN)
            else:
                await sm.edit_text("⚠️ Screenshot service unavailable.")
            bot_status["message_count"] += 1
            return

        # Text search mode
        sm = await u.message.reply_text("🔎 *Searching…* 🐾", parse_mode=ParseMode.MARKDOWN)
        await c.bot.send_chat_action(cid, "typing")

        loop = asyncio.get_running_loop()
        wiki, goog = await asyncio.gather(
            loop.run_in_executor(None, wiki_summary, query),
            loop.run_in_executor(None, google_search, query)
        )

        # Let AI produce a single clean summary — no message spam
        summary = await ai_summarise(query, wiki, goog)

        try:
            await sm.delete()
        except Exception:
            pass

        if summary:
            header = f"🔍 *{query}*\n{'━'*30}\n\n"
            footer = ""
            if wiki["found"]:
                footer = f"\n\n📖 [Wikipedia: {wiki['title']}]({wiki['url']})"

            msg = header + summary + footer
            try:
                await u.message.reply_text(
                    msg, parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True)
            except Exception:
                # Markdown parse error fallback
                await u.message.reply_text(
                    clean_html(header + summary),
                    disable_web_page_preview=True)
        else:
            await u.message.reply_text(
                f"😿 No results for *{query}*.",
                parse_mode=ParseMode.MARKDOWN)

        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[search_handler] {e}", exc_info=True)
        bot_status["error_count"] += 1
        try:
            await u.message.reply_text("😿 Search failed. Try again.")
        except Exception:
            pass

# ══════════════════════════════════════════
#  QUIZ
# ══════════════════════════════════════════
QUIZ_TOPICS = [
    "deep ocean biology","quantum mechanics","human brain","solar system",
    "animal behaviour","photosynthesis","black holes","DNA genetics",
    "volcanoes","ancient Egypt","World War 2","Roman Empire",
    "Renaissance art","space race","medieval history","Cold War",
    "ancient Greece","African geography","South America","European history",
    "island nations","famous inventors","social media history",
    "video game history","Oscar winners","internet history",
    "FIFA World Cup","Olympic records","cricket history",
    "NBA history","tennis grand slams","world cuisines",
    "mythology gods","cryptocurrency","stock market","AI history",
    "famous scientists","famous authors","music history",
    "NASA space exploration","dinosaurs","ocean geography",
    "chemistry elements","mathematics","astronomy","biology",
]

FALLBACK_QS = [
    {"q":"Which planet has the most confirmed moons?",
     "opts":["Jupiter","Saturn","Uranus","Neptune"],"ans":1,
     "fact":"Saturn has 146 confirmed moons as of 2024!"},
    {"q":"What covers ~71% of Earth's surface?",
     "opts":["Land","Ice","Water","Atmosphere"],"ans":2,
     "fact":"Oceans cover 71% of Earth — mostly unexplored!"},
    {"q":"Which country invented paper?",
     "opts":["Egypt","India","China","Greece"],"ans":2,
     "fact":"China invented paper ~105 AD in the Han dynasty."},
    {"q":"How many bones does an adult human have?",
     "opts":["186","196","206","216"],"ans":2,
     "fact":"Adults have 206 bones. Babies are born with ~270!"},
    {"q":"What is the fastest land animal?",
     "opts":["Lion","Cheetah","Greyhound","Pronghorn"],"ans":1,
     "fact":"Cheetahs reach 112 km/h in short bursts!"},
    {"q":"Which element has symbol 'Au'?",
     "opts":["Silver","Aluminium","Gold","Argon"],"ans":2,
     "fact":"Au comes from Latin 'aurum' meaning gold."},
    {"q":"Which ocean is the largest?",
     "opts":["Atlantic","Indian","Arctic","Pacific"],"ans":3,
     "fact":"Pacific covers more area than all land combined!"},
    {"q":"When did the first iPhone launch?",
     "opts":["2005","2006","2007","2008"],"ans":2,
     "fact":"Steve Jobs revealed the iPhone on Jan 9, 2007."},
    {"q":"What gas do plants absorb in photosynthesis?",
     "opts":["Oxygen","Nitrogen","CO2","Hydrogen"],"ans":2,
     "fact":"Plants take CO₂ and release O₂ — opposite of us!"},
    {"q":"How many sides does a hexagon have?",
     "opts":["5","6","7","8"],"ans":1,
     "fact":"Hex = 6 in Greek. Honeycombs are hexagonal!"},
]

def quiz_on_cooldown(cid: str, question: str) -> bool:
    now = time.time()
    return now < quiz_cooldown.get(cid, {}).get(q_hash(question), 0)

def mark_quiz(cid: str, question: str):
    now = time.time()
    quiz_cooldown.setdefault(cid, {})
    quiz_cooldown[cid] = {k: v for k, v in quiz_cooldown[cid].items() if v > now}
    quiz_cooldown[cid][q_hash(question)] = now + 3600

async def gen_quiz(topic: str, cid: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    for _ in range(3):
        try:
            ctx = await asyncio.wait_for(
                loop.run_in_executor(None, google_quiz_ctx, topic), timeout=8)
        except Exception:
            ctx = ""

        ctx_line = f"\nGoogle context: {ctx}" if ctx else ""
        system = (
            "Trivia quiz master. Output ONLY raw JSON. "
            "No markdown fences, no explanation."
        )
        user = (
            f"Topic: '{topic}'.{ctx_line}\n"
            "Generate ONE factual multiple-choice question.\n"
            "Rules: specific fact, 4 options, include fun_fact.\n"
            '{"question":"...","options":["A","B","C","D"],'
            '"correct_index":0,"fun_fact":"..."}'
        )
        raw = await ai(system, user, "", max_tok=280)
        if not raw:
            continue
        try:
            cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
            m = re.search(r"\{[\s\S]+\}", cleaned)
            if not m:
                continue
            d    = json.loads(m.group(0))
            q    = str(d.get("question","")).strip()
            opts = d.get("options",[])
            idx  = int(d.get("correct_index",0))
            fact = str(d.get("fun_fact","Beluga knows all! 🐾")).strip()
            if not q or len(opts) != 4 or not (0 <= idx <= 3):
                continue
            if quiz_on_cooldown(cid, q):
                continue
            return {"question":q,"options":opts,"correct_index":idx,"fun_fact":fact}
        except Exception as e:
            logger.debug(f"[Quiz parse] {e}")
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 and parts[1].strip() else random.choice(QUIZ_TOPICS)
        cid   = str(u.effective_chat.id)
        cid_i = u.effective_chat.id

        await safe_react(c.bot, cid_i, u.message.message_id, "💡")
        await c.bot.send_chat_action(cid_i, "typing")
        sm = await u.message.reply_text("🎲 Generating quiz… 🐈")

        qdata = await gen_quiz(topic, cid)

        try:
            await sm.delete()
        except Exception:
            pass

        if qdata:
            mark_quiz(cid, qdata["question"])
            try:
                pm = await c.bot.send_poll(
                    chat_id=cid_i,
                    question=f"🐱 {qdata['question'][:255]}",
                    options=[str(o)[:100] for o in qdata["options"]],
                    type="quiz",
                    correct_option_id=qdata["correct_index"],
                    is_anonymous=False,
                    explanation=qdata["fun_fact"][:200]
                )
                active_polls[pm.poll.id] = {
                    "chat_id":       cid_i,
                    "correct_index": qdata["correct_index"],
                    "topic":         topic,
                }
                bot_status["message_count"] += 1
                return
            except Exception as e:
                logger.error(f"[Quiz/send_poll] {e}")

        # Fallback
        now  = time.time()
        used = {h for h, exp in quiz_cooldown.get(cid, {}).items() if exp > now}
        avail = [fb for fb in FALLBACK_QS if q_hash(fb["q"]) not in used]
        if not avail:
            avail = FALLBACK_QS
        fb = random.choice(avail)
        mark_quiz(cid, fb["q"])
        pm = await c.bot.send_poll(
            chat_id=cid_i,
            question=f"🐱 {fb['q']}",
            options=fb["opts"],
            type="quiz",
            correct_option_id=fb["ans"],
            is_anonymous=False,
            explanation=fb["fact"]
        )
        active_polls[pm.poll.id] = {"chat_id": cid_i, "correct_index": fb["ans"], "topic": topic}
        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[quiz_handler] {e}", exc_info=True)
        bot_status["error_count"] += 1
        try:
            await u.message.reply_text("😿 Quiz failed — try again!")
        except Exception:
            pass

# ══════════════════════════════════════════
#  POLL ANSWER → score
# ══════════════════════════════════════════
async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        ans  = u.poll_answer
        if not ans:
            return
        info = active_polls.get(ans.poll_id)
        if not info or not ans.option_ids:
            return
        if ans.option_ids[0] != info["correct_index"]:
            return

        cid  = str(info["chat_id"])
        uid  = str(ans.user.id)
        name = (ans.user.first_name or "?")[:30]
        new_score = update_score(cid, uid, name, +10)
        await async_github_save()
        logger.info(f"[Score] +10 → {name} ({uid}) = {new_score} in chat {cid}")
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
        cid    = str(u.effective_chat.id)
        scores = db.get("scores",{}).get(cid,{})

        if not scores:
            await u.message.reply_text(
                "📊 No scores yet!\nPlay `/quiz` to earn points 🐾",
                parse_mode=ParseMode.MARKDOWN)
            return

        board = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        lines = [
            "╔════════════════════════════╗",
            "🏆  *QUIZ LEADERBOARD*  🏆",
            "╚════════════════════════════╝\n",
        ]
        for i, e in enumerate(board[:10]):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
            name  = e["name"][:18]
            pts   = e["score"]
            lines.append(f"{medal} {name:<18} —  *{pts} pts*")

        lines += [
            "\n━━━━━━━━━━━━━━━━━━━━",
            "📈 Sorted: Highest → Lowest",
            "━━━━━━━━━━━━━━━━━━━━",
            "_+10 pts per correct answer_",
        ]
        await u.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[lb_handler] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ══════════════════════════════════════════
#  /pump  /dump  (OWNER ONLY — reply to user)
# ══════════════════════════════════════════
async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /pump 80000  (reply to target user) → +80000 pts
    /dump 80000  (reply to target user) → -80000 pts
    Only works if sender is OWNER_ID.
    """
    if not u.message:
        return
    try:
        sender_id = u.effective_user.id if u.effective_user else 0

        # Owner check
        if not is_owner(sender_id):
            await u.message.reply_text("🚫 Owner-only command.")
            return

        # Must be a reply
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text(
                "⚠️ Reply to a user's message with this command.\n"
                "Example: reply + `/pump 80000`",
                parse_mode=ParseMode.MARKDOWN)
            return

        # Parse amount
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await u.message.reply_text("⚠️ Usage: `/pump 80000` or `/dump 80000`",
                                       parse_mode=ParseMode.MARKDOWN)
            return

        amount    = int(parts[1])
        cmd       = parts[0].lstrip("/").lower().split("@")[0]
        delta     = +amount if cmd == "pump" else -amount

        target    = u.message.reply_to_message.from_user
        cid       = str(u.effective_chat.id)
        uid       = str(target.id)
        name      = (target.first_name or "User")[:30]

        new_score = update_score(cid, uid, name, delta)
        await async_github_save()

        emoji = "🚀" if cmd == "pump" else "📉"
        sign  = "+" if delta > 0 else ""
        msg = (
            f"{emoji} *{'PUMP' if cmd=='pump' else 'DUMP'}*\n\n"
            f"👤 *{name}*\n"
            f"{'📈' if delta>0 else '📉'} Points: {sign}{amount:,}\n"
            f"💰 New Total: *{new_score:,} pts*"
        )
        await u.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[pump_dump] {e}", exc_info=True)
        bot_status["error_count"] += 1
        try:
            await u.message.reply_text("😿 Command failed.")
        except Exception:
            pass

# ══════════════════════════════════════════
#  FUN COMMANDS  /gay  /couple
# ══════════════════════════════════════════
GAY_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *ATTENTION EVERYONE* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nAfter deep investigation:\n\n👉 *{u}* 👈\n\nis… 🌈✨ *SUPER GAY* ✨🌈\n\nMust slay forever 💅😭\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📡 *GOVERNMENT ALERT* 📡\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRainbow activity from:\n\n👉 *{u}* 👈\n\n🌈 *Certified Gay Citizen* 🌈\nToo fabulous! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
]
COUPLE_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💘 *LOVE DETECTOR 3000* 💘\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nPerfect couple:\n\n👉 *{u1}* ❤️ *{u2}* 👈\n\nCompatibility: ██████████ 100%\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *COUPLE ALERT* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRomantic activity:\n\n👉 *{u1}* 💞 *{u2}* 👈\n\n💖 *OFFICIAL COUPLE* 💖\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid   = str(u.effective_chat.id)
        cmd   = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
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
        owner_cmds = "\n• `/pump <pts>` `/dump <pts>` — Owner: adjust points" if OWNER_ID else ""
        text = (
            "```\n"
            "╔══════════════════════════════════════╗\n"
            "         🐱  BELUGA AI BOT  🐱         \n"
            "╚══════════════════════════════════════╝\n"
            "```\n\n"
            "💬 *Smart Telegram Chat Bot v4*\n\n"
            "⚡ *Commands:*\n"
            "• `/search <topic>` — AI-powered smart summary\n"
            "• `/search <url>` — Website screenshot\n"
            "• `/quiz` — Random trivia (no repeats 1h)\n"
            "• `/quiz crypto` — Topic-specific quiz\n"
            "• `/lb` — Quiz leaderboard 🏆\n"
            "• `/gay` `/couple` — Fun daily commands\n"
            f"• Mention *beluga* — AI chat 🐾{owner_cmds}\n\n"
            "👋 _Start chatting now!_"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[start_handler] {e}", exc_info=True)

# ══════════════════════════════════════════
#  MONITOR
# ══════════════════════════════════════════
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot:
        return
    try:
        uid  = u.effective_user.id
        cid  = str(u.effective_chat.id)
        now  = datetime.now()

        # Spam guard
        spam_tracker.setdefault(uid, [])
        spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
        spam_tracker[uid].append(now)
        if len(spam_tracker[uid]) >= 4:
            try: await u.message.delete()
            except Exception: pass
            return

        # Track user
        db.setdefault("seen",{}).setdefault(cid,{})[str(uid)] = {
            "id": uid,
            "un": u.effective_user.username,
            "n":  u.effective_user.first_name or "User",
        }
        db.setdefault("counts",{})[cid] = db["counts"].get(cid, 0) + 1
        save_db()

        if db["counts"][cid] % 6 == 0:
            await safe_react(c.bot, u.effective_chat.id, u.message.message_id)

        text     = (u.message.text or "").strip()
        text_low = text.lower()

        beluga   = "beluga" in text_low
        reply_me = (u.message.reply_to_message and
                    u.message.reply_to_message.from_user and
                    u.message.reply_to_message.from_user.id == c.bot.id)
        mention  = any(
            "beluga" in text_low[e.offset:e.offset+e.length]
            for e in (u.message.entities or [])
            if e.type == "mention"
        )

        if beluga or reply_me or mention:
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
        logger.debug(f"[Net] {type(err).__name__}")
        return
    if isinstance(err, RetryAfter):
        logger.warning(f"[RateLimit] wait {err.retry_after}s")
        await asyncio.sleep(err.retry_after + 1)
        return
    if isinstance(err, (Forbidden, BadRequest)):
        logger.debug(f"[{type(err).__name__}] {err}")
        return
    if isinstance(err, InvalidToken):
        logger.critical("❌ BOT TOKEN REJECTED")
        bot_status["running"] = False
        return
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logger.error(f"[UnhandledError]\n{tb}")
    bot_status["error_count"] += 1

# ══════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════
async def main():
    logger.info("=" * 55)
    logger.info("🐱  BELUGA BOT  v4.0.0")
    logger.info(f"   PORT={HTTP_PORT}  OWNER={OWNER_ID}")
    logger.info(f"   GitHub={'✅' if GITHUB_TOKEN and GITHUB_GIST else '❌ (local only)'}")
    logger.info("=" * 55)

    # ── 1. HTTP FIRST (Render health check needs this immediately)
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    # ── 2. Load scores from GitHub Gist
    loop = asyncio.get_running_loop()
    if GITHUB_TOKEN and GITHUB_GIST:
        try:
            gh_scores = await asyncio.wait_for(
                loop.run_in_executor(None, github_load_scores), timeout=15)
            if gh_scores:
                db["scores"] = gh_scores
                save_db()
        except Exception as e:
            logger.warning(f"[GitHub startup load] {e}")

    # ── 3. Build PTB
    app = TGApp.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",              start_handler))
    app.add_handler(CommandHandler("search",             search_handler))
    app.add_handler(CommandHandler("quiz",               quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler(["gay","couple"],     fun_dispatcher))
    app.add_handler(CommandHandler(["pump","dump"],      pump_dump_handler))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)
    logger.info("✅ Handlers registered")

    # ── 4. Start polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    bot_status["running"] = True
    logger.info("✅ Beluga is LIVE 🐱")

    # ── 5. Keep alive
    stop_evt = asyncio.Event()
    try:
        import signal
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT,  stop_evt.set)
    except (NotImplementedError, RuntimeError):
        pass

    try:
        await stop_evt.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    # ── 6. Graceful shutdown
    bot_status["running"] = False
    logger.info("🔄 Shutdown…")
    if GITHUB_TOKEN and GITHUB_GIST:
        try:
            await loop.run_in_executor(None, github_save_scores)
        except Exception:
            pass
    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try: await fn()
        except Exception: pass
    logger.info("✅ Done")

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
