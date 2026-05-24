# ═══════════════════════════════════════════════════════════════
#  BELUGA BOT  v5.0.0
#  Features: /search /quiz /lb /pump /dump /gay /couple
#            /tictac (PvP + vs Bot)
#            /rock   (PvP + vs Bot)
#            Auto media download (YT/Instagram/X)
#            GitHub Gist persistence
#            Always-200 health server
# ═══════════════════════════════════════════════════════════════

import os, logging, random, json, asyncio, requests, re
import urllib.parse, traceback, sys, hashlib, time, tempfile, shutil
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import web

from telegram import (
    Update, ReactionTypeEmoji,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application as TGApp, CommandHandler, ContextTypes,
    MessageHandler, PollAnswerHandler,
    CallbackQueryHandler, filters
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
DATA_FILE    = "beluga_brain.json"
OR_KEY       = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY     = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
HTTP_PORT    = int(os.environ.get("PORT", "10000"))
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_GIST  = os.environ.get("GITHUB_GIST_ID", "")

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN missing")
    sys.exit(1)

# ══════════════════════════════════════════
#  RUNTIME STATE
# ══════════════════════════════════════════
bot_status = {
    "running": False, "start_time": datetime.now(),
    "last_update": datetime.now(), "message_count": 0,
    "error_count": 0, "api_calls": 0, "failed_apis": 0,
}

quiz_cooldown: dict[str, dict[str, float]] = {}
active_polls:  dict[str, dict]             = {}
spam_tracker:  dict[int, list]             = {}
db:            dict                        = {}

# Game state dicts
ttt_games: dict[str, dict] = {}   # message_id → game state
rps_games: dict[str, dict] = {}   # message_id → game state
# Track which users are in active games (uid → game_key)
user_in_game: dict[str, str] = {}

GAME_TIMEOUT = 300  # 5 minutes

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

db = load_db()
db.setdefault("scores", {})

# ══════════════════════════════════════════
#  GITHUB GIST PERSISTENCE
# ══════════════════════════════════════════
GIST_FILENAME = "beluga_scores.json"

def github_load_scores() -> dict:
    if not GITHUB_TOKEN or not GITHUB_GIST:
        return {}
    try:
        r = requests.get(
            f"https://api.github.com/gists/{GITHUB_GIST}",
            headers={"Authorization": f"token {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github.v3+json"},
            timeout=10)
        if r.status_code == 200:
            files = r.json().get("files", {})
            if GIST_FILENAME in files:
                scores = json.loads(files[GIST_FILENAME].get("content", "{}"))
                logger.info(f"[GitHub] Loaded scores ({len(scores)} chats)")
                return scores
    except Exception as e:
        logger.error(f"[GitHub Load] {e}")
    return {}

def github_save_scores() -> bool:
    if not GITHUB_TOKEN or not GITHUB_GIST:
        return False
    try:
        r = requests.patch(
            f"https://api.github.com/gists/{GITHUB_GIST}",
            headers={"Authorization": f"token {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github.v3+json"},
            json={"files": {GIST_FILENAME: {
                "content": json.dumps(db.get("scores", {}), indent=2)
            }}},
            timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"[GitHub Save] {e}")
    return False

async def async_github_save():
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, github_save_scores)
    except Exception:
        pass

def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {"name": name, "score": 0})
    e["name"]  = name
    e["score"] = max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    save_db()
    return e["score"]

# ══════════════════════════════════════════
#  HTTP SERVER — always 200
# ══════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({
        "status": "healthy", "uptime_seconds": up,
        "running": bot_status["running"],
        "messages": bot_status["message_count"],
        "version": "5.0.0",
    }, status=200)

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()}, status=200)

async def _stats(req):
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    ok = bot_status["api_calls"] - bot_status["failed_apis"]
    return web.json_response({
        "uptime_hours": round(up/3600, 2),
        "messages": bot_status["message_count"],
        "errors": bot_status["error_count"],
        "success_rate": round(ok/max(bot_status["api_calls"],1)*100, 2),
    }, status=200)

async def start_http(port: int):
    aio = web.Application()
    aio.router.add_get("/",       _ping)
    aio.router.add_get("/ping",   _ping)
    aio.router.add_get("/health", _health)
    aio.router.add_get("/stats",  _stats)
    runner = web.AppRunner(aio)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ HTTP 0.0.0.0:{port}  /health /ping /stats")
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

def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

def now_ts() -> float:
    return time.time()

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
            timeout=12)
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
            timeout=12)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[OR] {e}")
        bot_status["failed_apis"] += 1
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 400) -> str:
    loop = asyncio.get_running_loop()
    hint = lang_hint(user)
    for fn in [_groq_sync, _or_sync]:
        try:
            res = await asyncio.wait_for(
                loop.run_in_executor(None, fn, system, hint, max_tok), timeout=14)
            if res:
                return res
        except Exception:
            pass
    return fallback

async def ai_emoji(text: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        r = await asyncio.wait_for(
            loop.run_in_executor(None, _groq_sync,
                "Output ONE emoji matching the emotion. ONLY the emoji.",
                f"Text: '{text[:60]}'", 10), timeout=6)
        if r:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", r)
            if found:
                return found[0][0]
    except Exception:
        pass
    return "😼"

# ══════════════════════════════════════════
#  WIKIPEDIA + GOOGLE + AI SEARCH
# ══════════════════════════════════════════
WIKI_UA = {"User-Agent": "BelugaBot/5.0"}
G_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def wiki_summary(query: str) -> dict:
    out = {"found": False, "title": "", "url": "", "intro": "", "sections": []}
    try:
        sr = requests.get("https://en.wikipedia.org/w/api.php",
            params={"action":"query","list":"search","srsearch":query,
                    "srlimit":5,"format":"json"},
            headers=WIKI_UA, timeout=10)
        hits = sr.json().get("query",{}).get("search",[])
        if not hits:
            return out
        ql = query.lower().strip()
        best = hits[0]["title"]
        for h in hits:
            if h["title"].lower() == ql:
                best = h["title"]; break
        er = requests.get("https://en.wikipedia.org/w/api.php",
            params={"action":"query","titles":best,"prop":"extracts|info",
                    "inprop":"url","explaintext":"true",
                    "exsectionformat":"wiki","format":"json"},
            headers=WIKI_UA, timeout=15)
        for pid, page in er.json().get("query",{}).get("pages",{}).items():
            if pid == "-1": continue
            raw = page.get("extract","").strip()
            url = page.get("fullurl",
                f"https://en.wikipedia.org/wiki/{urllib.parse.quote(best.replace(' ','_'))}")
            if not raw: continue
            parts = re.split(r"\n(==+)\s*(.+?)\s*\1\n", raw)
            intro = parts[0].strip()
            sections, i = [], 1
            while i + 2 < len(parts):
                st = parts[i+1].strip(); sb = parts[i+2].strip() if i+2 < len(parts) else ""
                if sb and st not in ("See also","References","Further reading",
                    "External links","Notes","Bibliography","Citations"):
                    sections.append({"h": st, "b": sb[:800]})
                i += 3
            out.update({"found":True,"title":best,"url":url,
                        "intro":intro[:1200],"sections":sections[:8]})
            break
    except Exception as e:
        logger.debug(f"[Wiki] {e}")
    return out

def google_search(query: str) -> dict:
    out = {"found": False, "ai_answer": "", "featured": "", "snippets": []}
    try:
        r = requests.get(
            f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=5&hl=en",
            headers=G_HDR, timeout=10)
        if r.status_code != 200: return out
        html = r.text
        for pat in [
            r'data-attrid="wa:/description"[^>]*>[\s\S]{0,200}?<span[^>]*>([^<]{40,800})',
            r'class="[^"]*wDYxhc[^"]*"[\s\S]{0,100}?<span[^>]*>([A-Z][^<]{60,600})',
            r'class="[^"]*LGOjhe[^"]*"[^>]*>([^<]{40,600})',
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c = clean_html(m.group(1))
                if len(c) > 40: out["ai_answer"] = c[:800]; break
        for pat in [
            r'class="[^"]*hgKElc[^"]*"[^>]*>([\s\S]{30,500}?)</span',
            r'class="[^"]*IZ6rdc[^"]*"[^>]*>([\s\S]{30,500}?)</div',
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c = clean_html(m.group(1))
                if len(c) > 30 and c != out["ai_answer"]:
                    out["featured"] = c[:500]; break
        seen = set()
        for pat in [r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,350}?)</div']:
            for m in re.finditer(pat, html, re.DOTALL):
                t = clean_html(m.group(1))
                if len(t) > 40 and t not in seen:
                    seen.add(t); out["snippets"].append(t[:250])
                if len(out["snippets"]) >= 3: break
        out["found"] = bool(out["ai_answer"] or out["featured"] or out["snippets"])
    except Exception as e:
        logger.debug(f"[Google] {e}")
    return out

def google_quiz_ctx(topic: str) -> str:
    try:
        r = requests.get(
            f"https://www.google.com/search?q={urllib.parse.quote_plus(topic+' trivia facts')}&num=3&hl=en",
            headers=G_HDR, timeout=8)
        if r.status_code != 200: return ""
        bits = []
        for pat in [r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,250}?)</div']:
            for m in re.finditer(pat, r.text, re.DOTALL):
                t = clean_html(m.group(1))
                if len(t) > 30: bits.append(t[:180])
            if len(bits) >= 3: break
        return " | ".join(bits[:3])
    except Exception: return ""

async def ai_summarise(query: str, wiki: dict, goog: dict) -> str:
    ctx = []
    if goog["ai_answer"]: ctx.append(f"Google AI: {goog['ai_answer']}")
    if goog["featured"]:  ctx.append(f"Featured: {goog['featured']}")
    if goog["snippets"]:  ctx.append("Web:\n" + "\n".join(f"- {s}" for s in goog["snippets"]))
    if wiki["found"]:
        ctx.append(f"Wikipedia ({wiki['title']}):\n{wiki['intro']}")
        for sec in wiki["sections"][:3]:
            ctx.append(f"[{sec['h']}] {sec['b']}")
    if not ctx: return ""
    return await ai(
        "Smart assistant. Write a clean accurate summary. Use bullet points. "
        "Max 300 words. Telegram markdown (*bold*, - bullets). No links.",
        f"Query: {query}\n\nData:\n{chr(10).join(ctx)[:2800]}\n\nWrite summary:",
        "", max_tok=450)

# ══════════════════════════════════════════
#  MEDIA DOWNLOADER (YT / Instagram / X)
# ══════════════════════════════════════════
_MEDIA_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:(?:twitter\.com|x\.com)/\S+/status/\d+"
    r"|(?:instagram\.com)/(?:p|reel|tv)/[A-Za-z0-9_-]+"
    r"|(?:youtu\.be|youtube\.com/(?:watch|shorts))\S+)",
    re.IGNORECASE
)
_dl_tracker: dict[str, list] = {}

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
        ydl_opts = {
            "format": "bestvideo[ext=mp4][filesize<50M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<50M]/best[filesize<50M]",
            "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "max_filesize": 50*1024*1024, "socket_timeout": 20,
            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info: result["error"] = "No info"; return result
            result["title"] = (info.get("title", "") or "")[:80]
            downloaded = ydl.prepare_filename(info)
            for ext in [".mp4",".webm",".mkv",".mov",".jpg",".jpeg",".png",".gif"]:
                c = os.path.splitext(downloaded)[0] + ext
                if os.path.exists(c): downloaded = c; break
            if not os.path.exists(downloaded):
                files = os.listdir(outdir)
                if files: downloaded = os.path.join(outdir, files[0])
                else: result["error"] = "File not found"; return result
            sz = os.path.getsize(downloaded)
            if sz == 0: result["error"] = "Empty file"; return result
            if sz > 50*1024*1024: result["error"] = "Too large"; return result
            ext_l = os.path.splitext(downloaded)[1].lower()
            result.update({"ok": True, "path": downloaded,
                           "type": "image" if ext_l in (".jpg",".jpeg",".png",".gif") else "video"})
    except Exception as e:
        err = str(e)
        if any(x in err.lower() for x in ["private","login","age","unavailable","removed","403","404"]):
            result["error"] = "unavailable"
        else:
            result["error"] = err[:120]
    return result

async def download_and_send(u: Update, c: ContextTypes.DEFAULT_TYPE, url: str):
    cid = u.effective_chat.id
    if not _dl_rate_ok(str(cid)): return
    await c.bot.send_chat_action(cid, "upload_video")
    ul = url.lower()
    if "youtu" in ul:   platform, pemoji = "YouTube",    "▶️"
    elif "instagram" in ul: platform, pemoji = "Instagram", "📸"
    else:               platform, pemoji = "X (Twitter)", "🐦"
    tmpdir = tempfile.mkdtemp(prefix="beluga_dl_")
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _ydl_download, url, tmpdir), timeout=60.0)
        if not result["ok"]:
            if result["error"] == "unavailable":
                await safe_react(c.bot, cid, u.message.message_id, "🔒")
            return
        caption = f"{pemoji} *{platform}*"
        if result["title"] and result["title"].lower() not in ("","video","media"):
            caption += f"\n_{result['title'][:100]}_"
        await c.bot.send_chat_action(cid,
            "upload_photo" if result["type"] == "image" else "upload_video")
        with open(result["path"], "rb") as f:
            if result["type"] == "image":
                await u.message.reply_photo(photo=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
            else:
                await u.message.reply_video(video=f, caption=caption,
                    parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
        bot_status["message_count"] += 1
    except asyncio.TimeoutError:
        logger.debug(f"[DL] Timeout {url}")
    except Exception as e:
        logger.error(f"[DL] {e}")
        bot_status["error_count"] += 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ══════════════════════════════════════════
#  SCREENSHOT
# ══════════════════════════════════════════
async def screenshot(url: str) -> Optional[str]:
    if not url.startswith(("http://","https://")): url = "https://" + url
    svcs = [f"https://image.thum.io/get/width/1280/crop/800/{url}"]
    loop = asyncio.get_running_loop()
    for svc in svcs:
        try:
            r = await asyncio.wait_for(
                loop.run_in_executor(None,
                    lambda u=svc: requests.head(u, timeout=6, allow_redirects=True)), timeout=8)
            if r.status_code in (200,301,302): return svc
        except Exception: continue
    return None

# ══════════════════════════════════════════
#  /search
# ══════════════════════════════════════════
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text(
                "🐱 *Usage:*\n`/search Michael Jackson`\n`/search github.com`",
                parse_mode=ParseMode.MARKDOWN); return
        query = parts[1].strip()
        cid   = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "🔍")
        no_space  = " " not in query
        looks_url = no_space and (query.startswith(("http://","https://","www."))
                                  or re.search(r"\.[a-z]{2,6}(/|$)", query))
        if looks_url:
            sm = await u.message.reply_text("📸 Capturing… 🐾")
            pic = await screenshot(query)
            if pic:
                try:
                    await u.message.reply_photo(photo=pic,
                        caption=f"🌐 `{query[:60]}`", parse_mode=ParseMode.MARKDOWN)
                    await sm.delete()
                except Exception:
                    await sm.edit_text(f"🌐 `{query}`", parse_mode=ParseMode.MARKDOWN)
            else:
                await sm.edit_text("⚠️ Screenshot unavailable.")
            bot_status["message_count"] += 1; return
        sm = await u.message.reply_text("🔎 *Searching…* 🐾", parse_mode=ParseMode.MARKDOWN)
        await c.bot.send_chat_action(cid, "typing")
        loop = asyncio.get_running_loop()
        wiki, goog = await asyncio.gather(
            loop.run_in_executor(None, wiki_summary, query),
            loop.run_in_executor(None, google_search, query))
        summary = await ai_summarise(query, wiki, goog)
        try: await sm.delete()
        except Exception: pass
        if summary:
            footer = f"\n\n📖 [Wikipedia]({wiki['url']})" if wiki["found"] else ""
            msg    = f"🔍 *{query}*\n{'━'*28}\n\n{summary}{footer}"
            try:
                await u.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True)
            except Exception:
                await u.message.reply_text(clean_html(msg), disable_web_page_preview=True)
        else:
            await u.message.reply_text(f"😿 No results for *{query}*.",
                parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[search] {e}", exc_info=True)
        bot_status["error_count"] += 1
        try: await u.message.reply_text("😿 Search failed.")
        except Exception: pass

# ══════════════════════════════════════════
#  QUIZ
# ══════════════════════════════════════════
QUIZ_TOPICS = [
    "deep ocean biology","quantum mechanics","human brain","solar system",
    "animal behaviour","black holes","DNA genetics","volcanoes","ancient Egypt",
    "World War 2","Roman Empire","space race","Cold War","ancient Greece",
    "famous inventors","social media history","video game history",
    "FIFA World Cup","Olympic records","cricket history","NBA history",
    "mythology gods","cryptocurrency","stock market","AI history",
    "famous scientists","music history","NASA space exploration","dinosaurs",
    "chemistry elements","mathematics","astronomy","biology",
]

FALLBACK_QS = [
    {"q":"Which planet has the most confirmed moons?","opts":["Jupiter","Saturn","Uranus","Neptune"],"ans":1,"fact":"Saturn has 146 moons as of 2024!"},
    {"q":"What covers ~71% of Earth's surface?","opts":["Land","Ice","Water","Atmosphere"],"ans":2,"fact":"Oceans cover 71% of Earth!"},
    {"q":"Which country invented paper?","opts":["Egypt","India","China","Greece"],"ans":2,"fact":"China invented paper ~105 AD."},
    {"q":"How many bones does an adult human have?","opts":["186","196","206","216"],"ans":2,"fact":"Adults have 206 bones!"},
    {"q":"What is the fastest land animal?","opts":["Lion","Cheetah","Greyhound","Pronghorn"],"ans":1,"fact":"Cheetahs reach 112 km/h!"},
    {"q":"Which element has symbol 'Au'?","opts":["Silver","Aluminium","Gold","Argon"],"ans":2,"fact":"Au = aurum = gold in Latin."},
    {"q":"Which ocean is the largest?","opts":["Atlantic","Indian","Arctic","Pacific"],"ans":3,"fact":"Pacific covers more than all land!"},
    {"q":"When did the first iPhone launch?","opts":["2005","2006","2007","2008"],"ans":2,"fact":"Steve Jobs revealed it Jan 9, 2007."},
    {"q":"What gas do plants absorb in photosynthesis?","opts":["Oxygen","Nitrogen","CO2","Hydrogen"],"ans":2,"fact":"Plants take CO₂, release O₂!"},
    {"q":"How many sides does a hexagon have?","opts":["5","6","7","8"],"ans":1,"fact":"Hex = 6 in Greek!"},
]

def quiz_on_cooldown(cid: str, question: str) -> bool:
    return time.time() < quiz_cooldown.get(cid, {}).get(q_hash(question), 0)

def mark_quiz(cid: str, question: str):
    quiz_cooldown.setdefault(cid, {})
    quiz_cooldown[cid] = {k:v for k,v in quiz_cooldown[cid].items() if v > time.time()}
    quiz_cooldown[cid][q_hash(question)] = time.time() + 3600

async def gen_quiz(topic: str, cid: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    for _ in range(3):
        try: ctx = await asyncio.wait_for(loop.run_in_executor(None, google_quiz_ctx, topic), timeout=8)
        except Exception: ctx = ""
        raw = await ai(
            "Trivia quiz master. Output ONLY raw JSON. No markdown.",
            f"Topic: '{topic}'." + (f"\nContext: {ctx}" if ctx else "") +
            "\nGenerate ONE factual MC question with 4 options and fun_fact.\n"
            '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}',
            "", max_tok=280)
        if not raw: continue
        try:
            cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
            m = re.search(r"\{[\s\S]+\}", cleaned)
            if not m: continue
            d = json.loads(m.group(0))
            q = str(d.get("question","")).strip()
            opts = d.get("options",[])
            idx  = int(d.get("correct_index",0))
            fact = str(d.get("fun_fact","Beluga knows all! 🐾")).strip()
            if not q or len(opts) != 4 or not (0 <= idx <= 3): continue
            if quiz_on_cooldown(cid, q): continue
            return {"question":q,"options":opts,"correct_index":idx,"fun_fact":fact}
        except Exception as e:
            logger.debug(f"[Quiz parse] {e}")
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 and parts[1].strip() else random.choice(QUIZ_TOPICS)
        cid   = str(u.effective_chat.id)
        cid_i = u.effective_chat.id
        await safe_react(c.bot, cid_i, u.message.message_id, "💡")
        await c.bot.send_chat_action(cid_i, "typing")
        sm = await u.message.reply_text("🎲 Generating quiz… 🐈")
        qdata = await gen_quiz(topic, cid)
        try: await sm.delete()
        except Exception: pass
        if qdata:
            mark_quiz(cid, qdata["question"])
            try:
                pm = await c.bot.send_poll(
                    chat_id=cid_i, question=f"🐱 {qdata['question'][:255]}",
                    options=[str(o)[:100] for o in qdata["options"]],
                    type="quiz", correct_option_id=qdata["correct_index"],
                    is_anonymous=False, explanation=qdata["fun_fact"][:200])
                active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":qdata["correct_index"],"topic":topic}
                bot_status["message_count"] += 1; return
            except Exception as e:
                logger.error(f"[Quiz/poll] {e}")
        now = time.time()
        used   = {h for h,exp in quiz_cooldown.get(cid,{}).items() if exp > now}
        avail  = [fb for fb in FALLBACK_QS if q_hash(fb["q"]) not in used] or FALLBACK_QS
        fb     = random.choice(avail)
        mark_quiz(cid, fb["q"])
        pm = await c.bot.send_poll(
            chat_id=cid_i, question=f"🐱 {fb['q']}",
            options=fb["opts"], type="quiz", correct_option_id=fb["ans"],
            is_anonymous=False, explanation=fb["fact"])
        active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":fb["ans"],"topic":topic}
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[quiz] {e}", exc_info=True)
        bot_status["error_count"] += 1
        try: await u.message.reply_text("😿 Quiz failed!")
        except Exception: pass

async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        ans  = u.poll_answer
        if not ans: return
        info = active_polls.get(ans.poll_id)
        if not info or not ans.option_ids: return
        if ans.option_ids[0] != info["correct_index"]: return
        cid  = str(info["chat_id"])
        uid  = str(ans.user.id)
        name = (ans.user.first_name or "?")[:30]
        new_score = update_score(cid, uid, name, +10)
        await async_github_save()
        logger.info(f"[Score] +10 {name} = {new_score} pts")
    except Exception as e:
        logger.debug(f"[poll_answer] {e}")

# ══════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid    = str(u.effective_chat.id)
        scores = db.get("scores",{}).get(cid,{})
        if not scores:
            await u.message.reply_text("📊 No scores yet! Play `/quiz` to earn points 🐾",
                parse_mode=ParseMode.MARKDOWN); return
        board = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        lines = [
            "╔════════════════════════════╗",
            "🏆  *QUIZ LEADERBOARD*  🏆",
            "╚════════════════════════════╝\n",
        ]
        for i, e in enumerate(board[:10]):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
            lines.append(f"{medal} {e['name'][:18]:<18} —  *{e['score']:,} pts*")
        lines += ["\n━━━━━━━━━━━━━━━━━━━━",
                  "📈 Sorted: Highest → Lowest",
                  "━━━━━━━━━━━━━━━━━━━━",
                  "_+10 pts correct quiz  |  +20 pts games_"]
        await u.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[lb] {e}", exc_info=True)

# ══════════════════════════════════════════
#  /pump  /dump
# ══════════════════════════════════════════
async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner-only command."); return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("⚠️ Reply to a user's message.\nExample: reply + `/pump 80000`",
                parse_mode=ParseMode.MARKDOWN); return
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await u.message.reply_text("⚠️ Usage: `/pump 80000` or `/dump 80000`",
                parse_mode=ParseMode.MARKDOWN); return
        amount  = int(parts[1])
        cmd     = parts[0].lstrip("/").lower().split("@")[0]
        delta   = +amount if cmd == "pump" else -amount
        target  = u.message.reply_to_message.from_user
        cid     = str(u.effective_chat.id)
        new_sc  = update_score(cid, str(target.id), (target.first_name or "User")[:30], delta)
        await async_github_save()
        emoji   = "🚀" if cmd == "pump" else "📉"
        sign    = "+" if delta > 0 else ""
        await u.message.reply_text(
            f"{emoji} *{'PUMP' if cmd=='pump' else 'DUMP'}*\n\n"
            f"👤 *{target.first_name}*\n"
            f"{'📈' if delta>0 else '📉'} {sign}{amount:,} pts\n"
            f"💰 New Total: *{new_sc:,} pts*",
            parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[pump_dump] {e}", exc_info=True)

# ══════════════════════════════════════════
#  GAME PROTECTION HELPERS
# ══════════════════════════════════════════
def game_key(msg_id: int, cid: int) -> str:
    return f"{cid}:{msg_id}"

def register_player(uid: str, gkey: str):
    user_in_game[uid] = gkey

def release_player(uid: str):
    user_in_game.pop(uid, None)

def player_busy(uid: str) -> bool:
    gkey = user_in_game.get(uid)
    if not gkey: return False
    # Check if the game still exists
    if gkey in ttt_games: return True
    if gkey in rps_games: return True
    # Game gone — clean up stale reference
    release_player(uid)
    return False

async def cleanup_expired_games():
    """Called periodically to remove timed-out games."""
    now = time.time()
    for gkey in list(ttt_games.keys()):
        g = ttt_games[gkey]
        if now - g.get("created", now) > GAME_TIMEOUT:
            for uid in [str(g.get("x_id","")), str(g.get("o_id",""))]:
                release_player(uid)
            del ttt_games[gkey]
    for gkey in list(rps_games.keys()):
        g = rps_games[gkey]
        if now - g.get("created", now) > GAME_TIMEOUT:
            for uid in [str(g.get("p1_id","")), str(g.get("p2_id",""))]:
                release_player(uid)
            del rps_games[gkey]

# ══════════════════════════════════════════
#  TIC TAC TOE
# ══════════════════════════════════════════
TTT_EMPTY = "⬜"
TTT_X     = "🔴❌"
TTT_O     = "🔵⭕"

WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

def ttt_check_winner(board: list) -> Optional[str]:
    for a,b,cc in WINS:
        if board[a] == board[b] == board[cc] and board[a] != TTT_EMPTY:
            return board[a]
    return None

def ttt_is_draw(board: list) -> bool:
    return all(c != TTT_EMPTY for c in board) and not ttt_check_winner(board)

def ttt_bot_move(board: list) -> int:
    """Smart bot: win > block > center > corners > random"""
    # Try to win
    for i in range(9):
        if board[i] == TTT_EMPTY:
            board[i] = TTT_O
            if ttt_check_winner(board): board[i] = TTT_EMPTY; return i
            board[i] = TTT_EMPTY
    # Block player
    for i in range(9):
        if board[i] == TTT_EMPTY:
            board[i] = TTT_X
            if ttt_check_winner(board): board[i] = TTT_EMPTY; return i
            board[i] = TTT_EMPTY
    # Center
    if board[4] == TTT_EMPTY: return 4
    # Corners
    for i in [0,2,6,8]:
        if board[i] == TTT_EMPTY: return i
    # Any empty
    for i in range(9):
        if board[i] == TTT_EMPTY: return i
    return -1

def ttt_build_keyboard(board: list, disabled: bool = False) -> InlineKeyboardMarkup:
    syms = ["1","2","3","4","5","6","7","8","9"]
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx = row*3 + col
            cell = board[idx]
            label = cell if cell != TTT_EMPTY else TTT_EMPTY
            cb = f"ttt:noop:{idx}" if (cell != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            r.append(InlineKeyboardButton(label, callback_data=cb))
        rows.append(r)
    return InlineKeyboardMarkup(rows)

def ttt_build_text(g: dict) -> str:
    x_name = g["x_name"]
    o_name = g["o_name"]
    turn   = g["turn"]   # "X" or "O"
    status = g.get("status","playing")

    if status == "playing":
        cur_name   = x_name if turn == "X" else o_name
        cur_symbol = TTT_X  if turn == "X" else TTT_O
        status_line = f"🎯 *Turn:* {cur_name}  {cur_symbol}"
    elif status == "draw":
        status_line = "🤝 *Match Draw!*"
    else:
        winner_name = g.get("winner_name","")
        status_line = f"🏆 *{winner_name} Wins!*  🎁 +20 pts"

    # Board display
    board = g["board"]
    rows  = []
    for row in range(3):
        rows.append("  ".join(board[row*3+col] for col in range(3)))
    board_str = "\n".join(rows)

    return (
        f"🎮 *TIC TAC TOE*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ *{x_name}*   🆚   ⭕ *{o_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{board_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_line}"
    )

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games()
        user_a    = u.effective_user
        cid       = u.effective_chat.id
        cid_s     = str(cid)
        uid_a     = str(user_a.id)
        name_a    = (user_a.first_name or "Player")[:20]
        vs_bot    = True
        user_b_id = None
        name_b    = "🤖 Beluga Bot"

        # PvP if reply
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot    = False
                user_b_id = rb.id
                uid_b     = str(rb.id)
                name_b    = (rb.first_name or "Player2")[:20]
                if player_busy(uid_b):
                    await u.message.reply_text("⚠️ That player is already in a game!")
                    return

        if player_busy(uid_a):
            await u.message.reply_text("⚠️ You're already in a game! Finish it first.")
            return

        board = [TTT_EMPTY] * 9
        g = {
            "board":     board,
            "turn":      "X",
            "x_id":      user_a.id,
            "x_name":    name_a,
            "o_id":      user_b_id if not vs_bot else -1,
            "o_name":    name_b,
            "vs_bot":    vs_bot,
            "status":    "playing",
            "created":   time.time(),
            "chat_id":   cid,
        }

        kbd  = ttt_build_keyboard(board)
        text = ttt_build_text(g)
        msg  = await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                          reply_markup=kbd)

        gkey = game_key(msg.message_id, cid)
        ttt_games[gkey] = g
        register_player(uid_a, gkey)
        if not vs_bot:
            register_player(str(user_b_id), gkey)

        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[tictac] {e}", exc_info=True)

async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        data   = q.data   # "ttt:move:4" or "ttt:noop:4"
        parts  = data.split(":")
        if len(parts) != 3 or parts[0] != "ttt": return
        action = parts[1]
        idx    = int(parts[2])

        cid    = q.message.chat_id
        mid    = q.message.message_id
        gkey   = game_key(mid, cid)
        g      = ttt_games.get(gkey)

        if not g:
            await q.answer("⏰ Game expired or not found.", show_alert=True); return

        uid    = str(q.from_user.id)
        # Access check
        valid_x = uid == str(g["x_id"])
        valid_o = (uid == str(g["o_id"])) or (g["vs_bot"] and valid_x)

        if g["status"] != "playing":
            await q.answer("Game already ended!", show_alert=True); return

        if action == "noop":
            await q.answer("Cell already taken!", show_alert=True); return

        # Turn check
        if g["turn"] == "X" and not valid_x:
            if uid not in [str(g["x_id"]), str(g["o_id"])]:
                await q.answer("❌ You are not part of this game!", show_alert=True)
            else:
                await q.answer("Not your turn!", show_alert=True)
            return
        if g["turn"] == "O" and not g["vs_bot"] and uid != str(g["o_id"]):
            if uid not in [str(g["x_id"]), str(g["o_id"])]:
                await q.answer("❌ You are not part of this game!", show_alert=True)
            else:
                await q.answer("Not your turn!", show_alert=True)
            return

        # Make move
        board = g["board"]
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        winner_sym = ttt_check_winner(board)

        if winner_sym:
            winner_name = g["x_name"] if winner_sym == TTT_X else g["o_name"]
            winner_uid  = str(g["x_id"]) if winner_sym == TTT_X else str(g["o_id"])
            g["status"]       = "win"
            g["winner_name"]  = winner_name
            cid_s = str(cid)
            if not g["vs_bot"] or winner_sym == TTT_X:
                new_sc = update_score(cid_s, winner_uid, winner_name, +20)
                asyncio.create_task(async_github_save())
            text = ttt_build_text(g)
            kbd  = ttt_build_keyboard(board, disabled=True)
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)
            # Cleanup
            release_player(str(g["x_id"]))
            release_player(str(g["o_id"]))
            del ttt_games[gkey]
            return

        if ttt_is_draw(board):
            g["status"] = "draw"
            text = ttt_build_text(g)
            kbd  = ttt_build_keyboard(board, disabled=True)
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)
            release_player(str(g["x_id"]))
            release_player(str(g["o_id"]))
            del ttt_games[gkey]
            return

        # Switch turn
        g["turn"] = "O" if g["turn"] == "X" else "X"

        # Bot move
        if g["vs_bot"] and g["turn"] == "O":
            bot_idx = ttt_bot_move(board)
            if bot_idx >= 0:
                board[bot_idx] = TTT_O
                winner_sym = ttt_check_winner(board)
                if winner_sym:
                    g["status"]      = "win"
                    g["winner_name"] = g["o_name"]
                    text = ttt_build_text(g)
                    kbd  = ttt_build_keyboard(board, disabled=True)
                    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)
                    release_player(str(g["x_id"]))
                    del ttt_games[gkey]
                    return
                if ttt_is_draw(board):
                    g["status"] = "draw"
                    text = ttt_build_text(g)
                    kbd  = ttt_build_keyboard(board, disabled=True)
                    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)
                    release_player(str(g["x_id"]))
                    del ttt_games[gkey]
                    return
                g["turn"] = "X"

        text = ttt_build_text(g)
        kbd  = ttt_build_keyboard(board)
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)

    except Exception as e:
        logger.error(f"[ttt_callback] {e}", exc_info=True)

# ══════════════════════════════════════════
#  ROCK PAPER SCISSORS
# ══════════════════════════════════════════
RPS_CHOICES = {
    "rock":     "🪨 Rock",
    "paper":    "📄 Paper",
    "scissors": "✂️ Scissors",
}
RPS_WINS = {  # key beats value
    "rock":     "scissors",
    "scissors": "paper",
    "paper":    "rock",
}
RPS_BTN_LABELS = [
    ("🟥🪨 Rock",     "rock"),
    ("🟦📄 Paper",    "paper"),
    ("🟨✂️ Scissors", "scissors"),
]

def rps_keyboard(gkey: str) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(label, callback_data=f"rps:{gkey_part}:{choice}")
        for label, choice in RPS_BTN_LABELS
    ]]
    # Split into 3 separate rows for better mobile UX
    rows = [[InlineKeyboardButton(label, callback_data=f"rps:{gkey_part}:{choice}")]
            for label, choice in RPS_BTN_LABELS]
    # use short key in callback to avoid 64-byte limit
    rows = [[InlineKeyboardButton(label, callback_data=f"rps:pick:{choice}")]
            for label, choice in RPS_BTN_LABELS]
    return InlineKeyboardMarkup(rows)

def rps_build_text(g: dict) -> str:
    p1   = g["p1_name"]
    p2   = g["p2_name"]
    p1ch = g.get("p1_choice")
    p2ch = g.get("p2_choice")
    vs_bot = g["vs_bot"]
    status = g.get("status","waiting")

    p1_line = f"✅ *{p1}* — Choice locked 🔒" if p1ch else f"⌛ *{p1}* — Choosing..."
    p2_line = f"✅ *{p2}* — Choice locked 🔒" if p2ch else f"⌛ *{p2}* — Choosing..."

    if status == "waiting":
        result_block = "⏳ *Waiting for both players...*"
    elif status == "done":
        ch1 = RPS_CHOICES.get(p1ch,"?")
        ch2 = RPS_CHOICES.get(p2ch,"?")
        winner = g.get("winner","draw")
        if winner == "draw":
            result_block = (
                f"🪨📄✂️ *RESULT*\n\n"
                f"👤 *{p1}*: {ch1}\n"
                f"👤 *{p2}*: {ch2}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🤝 *Draw!*"
            )
        else:
            wn = g.get("winner_name","?")
            result_block = (
                f"🪨📄✂️ *RESULT*\n\n"
                f"👤 *{p1}*: {ch1}\n"
                f"👤 *{p2}*: {ch2}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"👑 *{wn} Wins!*  🎁 +20 pts"
            )
    else:
        result_block = "⏳ Waiting..."

    if status == "done":
        return f"🎮 *ROCK • PAPER • SCISSORS*\n━━━━━━━━━━━━━━━━━━━━\n{result_block}"

    return (
        f"🎮 *ROCK • PAPER • SCISSORS*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚔️  *{p1}*   🆚   *{p2}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{p1_line}\n"
        f"{p2_line}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{result_block}"
    )

async def rock_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games()
        user_a  = u.effective_user
        cid     = u.effective_chat.id
        uid_a   = str(user_a.id)
        name_a  = (user_a.first_name or "Player")[:20]
        vs_bot  = True
        uid_b   = None
        name_b  = "🤖 Beluga Bot"

        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot = False
                uid_b  = str(rb.id)
                name_b = (rb.first_name or "Player2")[:20]
                if player_busy(uid_b):
                    await u.message.reply_text("⚠️ That player is already in a game!"); return

        if player_busy(uid_a):
            await u.message.reply_text("⚠️ You're already in a game!"); return

        g = {
            "p1_id":    user_a.id,
            "p1_name":  name_a,
            "p2_id":    int(uid_b) if uid_b else -1,
            "p2_name":  name_b,
            "p1_choice": None,
            "p2_choice": None,
            "vs_bot":   vs_bot,
            "status":   "waiting",
            "created":  time.time(),
            "chat_id":  cid,
        }
        text = rps_build_text(g)
        # For RPS we embed the message_id in callback after sending
        msg = await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                         reply_markup=rps_keyboard("tmp"))
        gkey = game_key(msg.message_id, cid)
        rps_games[gkey] = g
        register_player(uid_a, gkey)
        if not vs_bot and uid_b:
            register_player(uid_b, gkey)
        # Re-send keyboard with real gkey embedded
        try:
            await msg.edit_reply_markup(rps_keyboard(gkey))
        except Exception:
            pass
        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[rock] {e}", exc_info=True)

async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        data  = q.data   # "rps:pick:rock"  or  "rps:{gkey}:{choice}"
        parts = data.split(":")
        if len(parts) < 3 or parts[0] != "rps": return

        action = parts[1]
        choice = parts[2]

        cid  = q.message.chat_id
        mid  = q.message.message_id
        gkey = game_key(mid, cid)
        g    = rps_games.get(gkey)

        if not g:
            await q.answer("⏰ Game expired.", show_alert=True); return
        if g["status"] == "done":
            await q.answer("Game already ended!", show_alert=True); return

        uid    = str(q.from_user.id)
        is_p1  = uid == str(g["p1_id"])
        is_p2  = (uid == str(g["p2_id"])) or (g["vs_bot"] and is_p1)

        if not is_p1 and not is_p2:
            await q.answer("❌ You are not part of this game!", show_alert=True); return

        if is_p1 and not g["vs_bot"]:
            if g["p1_choice"]:
                await q.answer("You already chose!", show_alert=True); return
            g["p1_choice"] = choice
            await q.answer("✅ Choice locked!")
        elif is_p1 and g["vs_bot"]:
            if g["p1_choice"]:
                await q.answer("You already chose!", show_alert=True); return
            g["p1_choice"] = choice
            # Bot picks random
            g["p2_choice"] = random.choice(list(RPS_WINS.keys()))
            await q.answer("✅ Choice locked!")
        elif not is_p1 and is_p2:
            if g["p2_choice"]:
                await q.answer("You already chose!", show_alert=True); return
            g["p2_choice"] = choice
            await q.answer("✅ Choice locked!")

        # Both chosen?
        if g["p1_choice"] and g["p2_choice"]:
            c1, c2 = g["p1_choice"], g["p2_choice"]
            if c1 == c2:
                g["winner"]      = "draw"
                g["winner_name"] = ""
            elif RPS_WINS.get(c1) == c2:
                g["winner"]      = "p1"
                g["winner_name"] = g["p1_name"]
                new_sc = update_score(str(cid), str(g["p1_id"]), g["p1_name"], +20)
                asyncio.create_task(async_github_save())
            else:
                g["winner"]      = "p2"
                g["winner_name"] = g["p2_name"]
                if not g["vs_bot"]:
                    new_sc = update_score(str(cid), str(g["p2_id"]), g["p2_name"], +20)
                    asyncio.create_task(async_github_save())
            g["status"] = "done"
            text = rps_build_text(g)
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
            release_player(str(g["p1_id"]))
            release_player(str(g["p2_id"]))
            del rps_games[gkey]
        else:
            text = rps_build_text(g)
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=rps_keyboard(gkey))

    except Exception as e:
        logger.error(f"[rps_callback] {e}", exc_info=True)

# ══════════════════════════════════════════
#  FUN COMMANDS  /gay  /couple
# ══════════════════════════════════════════
GAY_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *ATTENTION* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nAfter deep investigation:\n👉 *{u}* 👈\nis 🌈✨ *SUPER GAY* ✨🌈\nMust slay forever 💅😭\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n📡 *GOVERNMENT ALERT* 📡\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRainbow activity from:\n👉 *{u}* 👈\n🌈 *Certified Gay Citizen* 🌈\nToo fabulous! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
]
COUPLE_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n💘 *LOVE DETECTOR 3000* 💘\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👉 *{u1}* ❤️ *{u2}* 👈\n\nCompatibility: ██████████ 100%\nMade for each other! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *COUPLE ALERT* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👉 *{u1}* 💞 *{u2}* 👈\n\n💖 *OFFICIAL COUPLE* 💖\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid   = str(u.effective_chat.id)
        cmd   = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        users = list(db.get("seen",{}).get(cid,{}).values())
        if len(users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text("Meow… need more chat members! 😿🐾"); return
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
        logger.error(f"[fun] {e}", exc_info=True)

# ══════════════════════════════════════════
#  /start
# ══════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        ocmds = "\n• `/pump` `/dump` — Owner: adjust points" if OWNER_ID else ""
        text = (
            "```\n╔══════════════════════════════════╗\n"
            "        🐱  BELUGA AI BOT  🐱      \n"
            "╚══════════════════════════════════╝\n```\n\n"
            "💬 *Smart Telegram Chat Bot v5*\n\n"
            "⚡ *Commands:*\n"
            "• `/search <topic>` — AI-powered smart summary\n"
            "• `/search <url>` — Website screenshot\n"
            "• `/quiz` — Random trivia  |  `/quiz crypto` — Topic\n"
            "• `/tictac` — Tic Tac Toe (reply user = PvP, else vs Bot)\n"
            "• `/rock` — Rock Paper Scissors (same)\n"
            "• `/lb` — Quiz leaderboard 🏆\n"
            "• `/gay` `/couple` — Fun daily commands\n"
            f"• Mention *beluga* — AI chat 🐾{ocmds}\n\n"
            "🎬 *Auto:* Send YouTube/Instagram/X links → I download them!\n\n"
            "👋 _Start chatting now!_"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[start] {e}", exc_info=True)

# ══════════════════════════════════════════
#  MONITOR
# ══════════════════════════════════════════
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
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
            "id": uid, "un": u.effective_user.username,
            "n": u.effective_user.first_name or "User",
        }
        db.setdefault("counts",{})[cid] = db["counts"].get(cid, 0) + 1
        save_db()

        if db["counts"][cid] % 6 == 0:
            await safe_react(c.bot, u.effective_chat.id, u.message.message_id)

        text     = (u.message.text or "").strip()
        text_low = text.lower()

        # ── Auto media download ───────────────────────────────
        media_match = _MEDIA_RE.search(text)
        if media_match:
            asyncio.create_task(
                download_and_send(u, c, media_match.group(0)))

        # ── AI chat trigger ───────────────────────────────────
        beluga   = "beluga" in text_low
        reply_me = (u.message.reply_to_message and
                    u.message.reply_to_message.from_user and
                    u.message.reply_to_message.from_user.id == c.bot.id)
        mention  = any(
            "beluga" in text_low[e.offset:e.offset+e.length]
            for e in (u.message.entities or []) if e.type == "mention")

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
        logger.debug(f"[Net] {type(err).__name__}"); return
    if isinstance(err, RetryAfter):
        logger.warning(f"[RateLimit] {err.retry_after}s")
        await asyncio.sleep(err.retry_after + 1); return
    if isinstance(err, (Forbidden, BadRequest)):
        logger.debug(f"[{type(err).__name__}] {err}"); return
    if isinstance(err, InvalidToken):
        logger.critical("❌ TOKEN REJECTED"); bot_status["running"] = False; return
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logger.error(f"[UnhandledError]\n{tb}")
    bot_status["error_count"] += 1

# ══════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════
async def main():
    logger.info("=" * 55)
    logger.info("🐱  BELUGA BOT  v5.0.0")
    logger.info(f"   PORT={HTTP_PORT}  OWNER={OWNER_ID}")
    logger.info(f"   GitHub={'✅' if GITHUB_TOKEN and GITHUB_GIST else '❌'}")
    logger.info("=" * 55)

    # 1. HTTP first — Render health check
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    # 2. Load GitHub scores
    loop = asyncio.get_running_loop()
    if GITHUB_TOKEN and GITHUB_GIST:
        try:
            gh = await asyncio.wait_for(
                loop.run_in_executor(None, github_load_scores), timeout=15)
            if gh: db["scores"] = gh; save_db()
        except Exception as e:
            logger.warning(f"[GitHub startup] {e}")

    # 3. Build PTB
    app = TGApp.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",              start_handler))
    app.add_handler(CommandHandler("search",             search_handler))
    app.add_handler(CommandHandler("quiz",               quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler(["gay","couple"],     fun_dispatcher))
    app.add_handler(CommandHandler(["pump","dump"],      pump_dump_handler))
    app.add_handler(CommandHandler("tictac",             tictac_handler))
    app.add_handler(CommandHandler("rock",               rock_handler))
    app.add_handler(CallbackQueryHandler(ttt_callback,   pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(rps_callback,   pattern=r"^rps:"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)
    logger.info("✅ Handlers registered")

    # 4. Start polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES)
    bot_status["running"] = True
    logger.info("✅ Beluga v5 is LIVE 🐱")

    # 5. Keep alive + periodic cleanup
    stop_evt = asyncio.Event()
    try:
        import signal
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT,  stop_evt.set)
    except (NotImplementedError, RuntimeError): pass

    async def periodic_cleanup():
        while not stop_evt.is_set():
            await asyncio.sleep(60)
            await cleanup_expired_games()

    cleanup_task = asyncio.create_task(periodic_cleanup())

    try:
        await stop_evt.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    # 6. Graceful shutdown
    cleanup_task.cancel()
    bot_status["running"] = False
    logger.info("🔄 Shutdown…")
    if GITHUB_TOKEN and GITHUB_GIST:
        try: await loop.run_in_executor(None, github_save_scores)
        except Exception: pass
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
        logger.critical("❌ FATAL: Invalid BOT_TOKEN"); sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ FATAL: {e}", exc_info=True); sys.exit(1)
