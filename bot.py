# ═══════════════════════════════════════════════════════════════
#  BELUGA BOT  v6.0.0  — FULLY FIXED WITH AUTO-DISCOVERY GIST
#  GitHub: auto-creates gist if missing, stores user_id+name+pts
#  yt-dlp: YouTube + Instagram auto-download on link detection
#  /tictac PvP + vs Bot with timer
#  /rock RPS PvP + vs Bot
#  /quiz /lb /pump /dump /search /gay /couple
#  /health /ping always 200
# ═══════════════════════════════════════════════════════════════

import os, logging, random, json, asyncio, requests, re
import urllib.parse, traceback, sys, hashlib, time, tempfile, shutil
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import web

from telegram import Update, ReactionTypeEmoji, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application as TGApp, CommandHandler, ContextTypes,
    MessageHandler, PollAnswerHandler, CallbackQueryHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest, RetryAfter, InvalidToken

# ══════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Beluga")

# ══════════════════════════════════════════════════════
#  CONFIG — all from env vars
# ══════════════════════════════════════════════════════
DATA_FILE    = "beluga_brain.json"
OR_KEY       = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY     = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
HTTP_PORT    = int(os.environ.get("PORT", "10000"))
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# GITHUB_GIST_ID is OPTIONAL — if blank, bot auto-creates one
GITHUB_GIST  = os.environ.get("GITHUB_GIST_ID", "").strip()

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN missing"); sys.exit(1)

# ══════════════════════════════════════════════════════
#  RUNTIME STATE
# ══════════════════════════════════════════════════════
bot_status = {
    "running": False, "start_time": datetime.now(),
    "last_update": datetime.now(), "message_count": 0,
    "error_count": 0, "api_calls": 0, "failed_apis": 0,
}
quiz_cooldown: dict[str, dict[str, float]] = {}
active_polls:  dict[str, dict]             = {}
spam_tracker:  dict[int, list]             = {}
db:            dict                        = {}
ttt_games:     dict[str, dict]             = {}
rps_games:     dict[str, dict]             = {}
user_in_game:  dict[str, str]              = {}
game_timers:   dict[str, dict]             = {}
GAME_TIMEOUT   = 300
TIMER_DURATION = 60
_dl_tracker:   dict[str, list]             = {}
_resolved_gist_id: str                     = ""   # cached after creation

# ══════════════════════════════════════════════════════
#  DATABASE (local JSON)
# ══════════════════════════════════════════════════════
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
db.setdefault("gist_id", "")   # persists auto-created gist id

# ══════════════════════════════════════════════════════
#  GITHUB GIST  — auto-create, load, save
#
#  Schema saved to gist (beluga_scores.json):
#  {
#    "chat_id_string": {
#      "user_id_string": {
#        "name": "First Name",
#        "user_id": 123456789,
#        "score": 480
#      },
#      ...
#    },
#    ...
#  }
# ══════════════════════════════════════════════════════
GIST_FILENAME = "beluga_scores.json"
GH_HEADERS = lambda: {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "Content-Type": "application/json",
}

def gh_find_existing_gist() -> str:
    """Scan user's gists to find one containing beluga_scores.json if local cache was wiped."""
    if not GITHUB_TOKEN:
        return ""
    try:
        r = requests.get(
            "https://api.github.com/gists",
            headers=GH_HEADERS(), timeout=15
        )
        if r.status_code == 200:
            for g in r.json():
                if GIST_FILENAME in g.get("files", {}):
                    gid = g.get("id", "")
                    logger.info(f"🔍 Found existing GitHub Gist automatically: {gid}")
                    return gid
    except Exception as e:
        logger.error(f"[GitHub Find] {e}")
    return ""

def gh_get_gist_id() -> str:
    """Return gist ID: env var → db cache → auto-discover → auto-create"""
    global _resolved_gist_id
    if _resolved_gist_id:
        return _resolved_gist_id
    if GITHUB_GIST:
        _resolved_gist_id = GITHUB_GIST
        return _resolved_gist_id
    if db.get("gist_id"):
        _resolved_gist_id = db["gist_id"]
        return _resolved_gist_id
    
    # Auto-discover from GitHub if local file was wiped
    discovered = gh_find_existing_gist()
    if discovered:
        _resolved_gist_id = discovered
        db["gist_id"] = discovered
        save_db()
        return discovered
        
    return ""

def gh_create_gist() -> str:
    """Create a new private gist and cache its ID."""
    global _resolved_gist_id
    if not GITHUB_TOKEN:
        return ""
    try:
        payload = {
            "description": "Beluga Bot Leaderboard — auto-created",
            "public": False,
            "files": {
                GIST_FILENAME: {
                    "content": json.dumps({}, indent=2)
                }
            }
        }
        r = requests.post(
            "https://api.github.com/gists",
            headers=GH_HEADERS(), json=payload, timeout=15
        )
        if r.status_code == 201:
            gid = r.json()["id"]
            _resolved_gist_id = gid
            db["gist_id"] = gid
            save_db()
            logger.info(f"✅ GitHub Gist created: {gid}")
            logger.info(f"   Add to Render env: GITHUB_GIST_ID={gid}")
            return gid
        else:
            logger.error(f"[GitHub Create] status {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[GitHub Create] {e}")
    return ""

def github_load_scores() -> dict:
    """Load scores from gist on startup."""
    if not GITHUB_TOKEN:
        logger.warning("[GitHub] No GITHUB_TOKEN — scores won't persist across restarts")
        return {}
    gid = gh_get_gist_id()
    if not gid:
        logger.info("[GitHub] No gist yet — will create on first save")
        return {}
    try:
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers=GH_HEADERS(), timeout=15
        )
        if r.status_code == 200:
            files = r.json().get("files", {})
            if GIST_FILENAME in files:
                raw = files[GIST_FILENAME].get("content", "{}")
                scores = json.loads(raw)
                logger.info(f"✅ Loaded scores from GitHub ({len(scores)} chats)")
                return scores
            else:
                logger.warning(f"[GitHub] File '{GIST_FILENAME}' not in gist")
        elif r.status_code == 404:
            logger.warning("[GitHub] Gist not found (404) — will create new one")
            db["gist_id"] = ""
            global _resolved_gist_id
            _resolved_gist_id = ""
            save_db()
        else:
            logger.warning(f"[GitHub Load] status {r.status_code}")
    except Exception as e:
        logger.error(f"[GitHub Load] {e}")
    return {}

def github_save_scores() -> bool:
    """Save scores to gist. Auto-creates gist if none exists."""
    if not GITHUB_TOKEN:
        return False
    gid = gh_get_gist_id()
    if not gid:
        gid = gh_create_gist()
        if not gid:
            logger.error("[GitHub] Cannot save — failed to create gist")
            return False

    scores = db.get("scores", {})
    # Ensure every entry has user_id stored
    for cid_s, users in scores.items():
        for uid_s, entry in users.items():
            entry.setdefault("user_id", int(uid_s) if uid_s.lstrip("-").isdigit() else 0)

    try:
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers=GH_HEADERS(),
            json={"files": {GIST_FILENAME: {"content": json.dumps(scores, indent=2)}}},
            timeout=15
        )
        if r.status_code == 200:
            logger.debug("✅ Scores saved to GitHub")
            return True
        else:
            logger.warning(f"[GitHub Save] status {r.status_code}: {r.text[:200]}")
            if r.status_code == 404:
                # Gist deleted externally — recreate
                global _resolved_gist_id
                _resolved_gist_id = ""
                db["gist_id"] = ""
                save_db()
    except Exception as e:
        logger.error(f"[GitHub Save] {e}")
    return False

async def async_github_save():
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, github_save_scores)
    except Exception as e:
        logger.debug(f"[Async GitHub] {e}")

def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {"name": name, "user_id": int(uid) if uid.lstrip("-").isdigit() else 0, "score": 0})
    e["name"]    = name
    e["user_id"] = int(uid) if uid.lstrip("-").isdigit() else 0
    e["score"]   = max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    save_db()
    return e["score"]

def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

# ══════════════════════════════════════════════════════
#  HTTP SERVER — always 200
# ══════════════════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    gid = gh_get_gist_id()
    return web.json_response({
        "status": "healthy", "uptime_seconds": up,
        "running": bot_status["running"],
        "messages": bot_status["message_count"],
        "errors": bot_status["error_count"],
        "github_gist": gid or "not configured",
        "version": "6.0.0",
        "ts": datetime.now().isoformat(),
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
        "api_calls": bot_status["api_calls"],
        "success_rate_pct": round(ok/max(bot_status["api_calls"],1)*100, 2),
        "active_ttt_games": len(ttt_games),
        "active_rps_games": len(rps_games),
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

_HINGLISH = ["kya","hai","kaise","bhai","batao","kr","rha","tha","ye","wo",
             "tu","tum","ko","nhi","aur","mujhe","hoga","karo","sab","dost"]

def lang_hint(text: str) -> str:
    lo = text.lower()
    if any(re.search(rf"\b{w}\b", lo) for w in _HINGLISH):
        return text + "\n\n[STRICT: Reply in Hinglish Roman script]"
    if any("\u0900" <= c <= "\u097F" for c in text):
        return text + "\n\n[STRICT: Reply in Hindi Devanagari]"
    return text + "\n\n[STRICT: Reply in fluent English]"

# ══════════════════════════════════════════════════════
#  AI ENGINE
# ══════════════════════════════════════════════════════
CHAT_PROMPT = (
    "You are Beluga — a cute, sassy, incredibly smart 2-year-old cat. "
    "NOT a bot, NOT an AI. Built by Team Oldy Crypto. "
    "Deny being a bot playfully. Never say 'As an AI'. "
    "Keep replies short (1-3 sentences), witty and conversational."
)

def _groq_sync(system: str, user: str, max_tok: int = 400) -> Optional[str]:
    if not GROQ_KEY: return None
    bot_status["api_calls"] += 1
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role":"system","content":system},{"role":"user","content":user}],
                  "max_tokens": max_tok}, timeout=12)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[Groq] {e}"); bot_status["failed_apis"] += 1
    return None

def _or_sync(system: str, user: str, max_tok: int = 400) -> Optional[str]:
    if not OR_KEY: return None
    bot_status["api_calls"] += 1
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://t.me/BelugaBot", "X-Title": "BelugaBot"},
            json={"model": "meta-llama/llama-3.3-70b-instruct:free",
                  "messages": [{"role":"system","content":system},{"role":"user","content":user}],
                  "max_tokens": max_tok}, timeout=12)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[OR] {e}"); bot_status["failed_apis"] += 1
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 400) -> str:
    loop = asyncio.get_running_loop()
    hint = lang_hint(user)
    for fn in [_groq_sync, _or_sync]:
        try:
            res = await asyncio.wait_for(loop.run_in_executor(None, fn, system, hint, max_tok), timeout=14)
            if res: return res
        except Exception: pass
    return fallback

async def ai_emoji(text: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        r = await asyncio.wait_for(
            loop.run_in_executor(None, _groq_sync,
                "Output ONE emoji matching emotion. ONLY the emoji.", f"Text: '{text[:60]}'", 10), timeout=6)
        if r:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", r)
            if found: return found[0][0]
    except Exception: pass
    return "😼"

# ══════════════════════════════════════════════════════
#  WIKIPEDIA + GOOGLE + AI SEARCH
# ══════════════════════════════════════════════════════
WIKI_UA = {"User-Agent": "BelugaBot/6.0"}
G_HDR   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Accept-Language": "en-US,en;q=0.9"}

def wiki_summary(query: str) -> dict:
    out = {"found": False, "title": "", "url": "", "intro": "", "sections": []}
    try:
        sr = requests.get("https://en.wikipedia.org/w/api.php",
            params={"action":"query","list":"search","srsearch":query,"srlimit":5,"format":"json"},
            headers=WIKI_UA, timeout=10)
        hits = sr.json().get("query",{}).get("search",[])
        if not hits: return out
        ql = query.lower().strip()
        best = hits[0]["title"]
        for h in hits:
            if h["title"].lower() == ql: best = h["title"]; break
        er = requests.get("https://en.wikipedia.org/w/api.php",
            params={"action":"query","titles":best,"prop":"extracts|info","inprop":"url",
                    "explaintext":"true","exsectionformat":"wiki","format":"json"},
            headers=WIKI_UA, timeout=15)
        for pid, page in er.json().get("query",{}).get("pages",{}).items():
            if pid == "-1": continue
            raw = page.get("extract","").strip()
            url = page.get("fullurl", f"https://en.wikipedia.org/wiki/{urllib.parse.quote(best.replace(' ','_'))}")
            if not raw: continue
            parts = re.split(r"\n(==+)\s*(.+?)\s*\1\n", raw)
            intro = parts[0].strip()
            sections, i = [], 1
            while i + 2 < len(parts):
                st = parts[i+1].strip(); sb = parts[i+2].strip() if i+2 < len(parts) else ""
                if sb and st not in ("See also","References","Further reading","External links","Notes","Bibliography","Citations"):
                    sections.append({"h": st, "b": sb[:800]})
                i += 3
            out.update({"found":True,"title":best,"url":url,"intro":intro[:1200],"sections":sections[:8]})
            break
    except Exception as e: logger.debug(f"[Wiki] {e}")
    return out

def google_search(query: str) -> dict:
    out = {"found": False, "ai_answer": "", "featured": "", "snippets": []}
    try:
        r = requests.get(f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=5&hl=en",
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
                c2 = clean_html(m.group(1))
                if len(c2) > 40: out["ai_answer"] = c2[:800]; break
        for pat in [r'class="[^"]*hgKElc[^"]*"[^>]*>([\s\S]{30,500}?)</span',
                    r'class="[^"]*IZ6rdc[^"]*"[^>]*>([\s\S]{30,500}?)</div']:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c2 = clean_html(m.group(1))
                if len(c2) > 30 and c2 != out["ai_answer"]: out["featured"] = c2[:500]; break
        seen = set()
        for m in re.finditer(r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,350}?)</div', html, re.DOTALL):
            t = clean_html(m.group(1))
            if len(t) > 40 and t not in seen:
                seen.add(t); out["snippets"].append(t[:250])
            if len(out["snippets"]) >= 3: break
        out["found"] = bool(out["ai_answer"] or out["featured"] or out["snippets"])
    except Exception as e: logger.debug(f"[Google] {e}")
    return out

def google_quiz_ctx(topic: str) -> str:
    try:
        r = requests.get(
            f"https://www.google.com/search?q={urllib.parse.quote_plus(topic+' trivia facts')}&num=3&hl=en",
            headers=G_HDR, timeout=8)
        if r.status_code != 200: return ""
        bits = []
        for m in re.finditer(r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,250}?)</div', r.text, re.DOTALL):
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
        for sec in wiki["sections"][:3]: ctx.append(f"[{sec['h']}] {sec['b']}")
    if not ctx: return ""
    return await ai(
        "Smart assistant. Write a clean accurate summary. Use bullet points. "
        "Max 300 words. Telegram markdown (*bold*, - bullets). No links.",
        f"Query: {query}\n\nData:\n{chr(10).join(ctx)[:2800]}\n\nWrite summary:", "", max_tok=450)

# ══════════════════════════════════════════════════════
#  MEDIA DOWNLOAD  — YouTube + Instagram + X
#  Uses yt-dlp (must be in requirements.txt)
# ══════════════════════════════════════════════════════
_MEDIA_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:"
    r"(?:twitter\.com|x\.com)/\S+?/status/\d+"
    r"|instagram\.com/(?:p|reel|tv|stories)/[A-Za-z0-9_\-]+"
    r"|(?:youtu\.be|youtube\.com/(?:watch|shorts|embed))\S+"
    r")",
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
    """
    Download with yt-dlp. Returns {ok, path, type, title, error}.
    Runs in executor — blocking I/O.
    """
    result = {"ok": False, "path": None, "type": "video", "title": "", "error": ""}
    try:
        import yt_dlp
    except ImportError:
        result["error"] = "yt_dlp not installed"
        logger.error("yt_dlp not installed — add yt-dlp to requirements.txt")
        return result

    ydl_opts = {
        # Best quality under 50MB, merge video+audio into mp4
        "format": (
            "bestvideo[ext=mp4][filesize<45M]+bestaudio[ext=m4a]"
            "/bestvideo[filesize<45M]+bestaudio"
            "/best[filesize<45M]"
            "/best"
        ),
        "outtmpl":       os.path.join(outdir, "%(id)s.%(ext)s"),
        "quiet":         True,
        "no_warnings":   True,
        "noplaylist":    True,
        "max_filesize":  49 * 1024 * 1024,
        "socket_timeout": 25,
        "retries":       3,
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                result["error"] = "No info extracted"; return result

            result["title"] = (info.get("title") or "")[:80]

            # Find downloaded file
            downloaded = ydl.prepare_filename(info)
            # Try common extensions including post-merge mp4
            for ext in [".mp4", ".webm", ".mkv", ".mov", ".m4v",
                        ".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                candidate = os.path.splitext(downloaded)[0] + ext
                if os.path.exists(candidate):
                    downloaded = candidate; break

            if not os.path.exists(downloaded):
                # Fallback: find any file in outdir
                files = [f for f in os.listdir(outdir) if not f.endswith(".part")]
                if not files:
                    result["error"] = "No file after download"; return result
                # Pick largest file
                files.sort(key=lambda f: os.path.getsize(os.path.join(outdir, f)), reverse=True)
                downloaded = os.path.join(outdir, files[0])

            sz = os.path.getsize(downloaded)
            if sz == 0:
                result["error"] = "Empty file"; return result
            if sz > 50 * 1024 * 1024:
                result["error"] = f"Too large ({sz//1024//1024}MB)"; return result

            ext_l = os.path.splitext(downloaded)[1].lower()
            ftype = "image" if ext_l in (".jpg",".jpeg",".png",".gif",".webp") else "video"
            result.update({"ok": True, "path": downloaded, "type": ftype})

    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ["private","login required","age","unavailable",
                                    "removed","suspended","not found","403","404",
                                    "this video is not available","members-only"]):
            result["error"] = "unavailable"
        else:
            result["error"] = str(e)[:150]
            logger.debug(f"[yt-dlp] {e}")

    return result

async def download_and_send(u: Update, c: ContextTypes.DEFAULT_TYPE, url: str):
    cid = u.effective_chat.id
    if not _dl_rate_ok(str(cid)):
        return  # silent rate limit

    url_l = url.lower()
    if "youtu"      in url_l: platform, pemoji = "YouTube",    "▶️"
    elif "instagram" in url_l: platform, pemoji = "Instagram", "📸"
    else:                      platform, pemoji = "X (Twitter)","🐦"

    # Show downloading status
    status_msg = None
    try:
        await c.bot.send_chat_action(cid, "upload_video")
        status_msg = await u.message.reply_text(
            f"⏳ *Downloading from {platform}…*",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    tmpdir = tempfile.mkdtemp(prefix="beluga_dl_")
    try:
        loop   = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _ydl_download, url, tmpdir),
            timeout=90.0)

        if status_msg:
            try: await status_msg.delete()
            except Exception: pass

        if not result["ok"]:
            if result["error"] == "unavailable":
                await safe_react(c.bot, cid, u.message.message_id, "🔒")
            else:
                logger.debug(f"[DL fail] {result['error']}")
            return

        caption = f"{pemoji} *Downloaded from {platform}*"
        if result["title"] and result["title"].lower() not in ("","video","media","reel"):
            caption += f"\n_{result['title'][:100]}_"
        caption += "\n✅ _via Beluga Bot_"

        await c.bot.send_chat_action(
            cid, "upload_photo" if result["type"]=="image" else "upload_video")

        with open(result["path"], "rb") as f:
            if result["type"] == "image":
                await u.message.reply_photo(
                    photo=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
            else:
                await u.message.reply_video(
                    video=f, caption=caption,
                    parse_mode=ParseMode.MARKDOWN, supports_streaming=True)

        bot_status["message_count"] += 1
        logger.info(f"[DL] ✅ {platform} sent in chat {cid}")

    except asyncio.TimeoutError:
        if status_msg:
            try: await status_msg.edit_text("⚠️ Download timed out. Video may be too large.")
            except Exception: pass
    except Exception as e:
        logger.error(f"[DL] {e}", exc_info=True)
        bot_status["error_count"] += 1
        if status_msg:
            try: await status_msg.edit_text("⚠️ Download failed.")
            except Exception: pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ══════════════════════════════════════════════════════
#  SCREENSHOT
# ══════════════════════════════════════════════════════
async def screenshot(url: str) -> Optional[str]:
    if not url.startswith(("http://","https://")): url = "https://" + url
    svc = f"https://image.thum.io/get/width/1280/crop/800/{url}"
    loop = asyncio.get_running_loop()
    try:
        r = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: requests.head(svc, timeout=6, allow_redirects=True)), timeout=8)
        if r.status_code in (200,301,302): return svc
    except Exception: pass
    return None

# ══════════════════════════════════════════════════════
#  /search
# ══════════════════════════════════════════════════════
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
                    await u.message.reply_photo(photo=pic, caption=f"🌐 `{query[:60]}`",
                        parse_mode=ParseMode.MARKDOWN)
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
            await u.message.reply_text(f"😿 No results for *{query}*.", parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[search] {e}", exc_info=True); bot_status["error_count"] += 1
        try: await u.message.reply_text("😿 Search failed.")
        except Exception: pass

# ══════════════════════════════════════════════════════
#  QUIZ
# ══════════════════════════════════════════════════════
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
        raw = await ai("Trivia quiz master. Output ONLY raw JSON. No markdown.",
            f"Topic: '{topic}'." + (f"\nContext: {ctx}" if ctx else "") +
            "\nGenerate ONE factual MC question with 4 options and fun_fact.\n"
            '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}',
            "", max_tok=280)
        if not raw: continue
        try:
            cleaned = re.sub(r"
http://googleusercontent.com/immersive_entry_chip/0
