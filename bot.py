# ═══════════════════════════════════════════════════════════════
#  BELUGA BOT  v6.0.0  — FULLY FIXED
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
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "Content-Type": "application/json",
}

def gh_get_gist_id() -> str:
    """Return gist ID: env var → db cache → auto-create"""
    global _resolved_gist_id
    if _resolved_gist_id:
        return _resolved_gist_id
    if GITHUB_GIST:
        _resolved_gist_id = GITHUB_GIST
        return _resolved_gist_id
    if db.get("gist_id"):
        _resolved_gist_id = db["gist_id"]
        return _resolved_gist_id
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
            cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
            m = re.search(r"\{[\s\S]+\}", cleaned)
            if not m: continue
            d    = json.loads(m.group(0))
            q    = str(d.get("question","")).strip()
            opts = d.get("options",[])
            idx  = int(d.get("correct_index",0))
            fact = str(d.get("fun_fact","Beluga knows all! 🐾")).strip()
            if not q or len(opts) != 4 or not (0 <= idx <= 3): continue
            if quiz_on_cooldown(cid, q): continue
            return {"question":q,"options":opts,"correct_index":idx,"fun_fact":fact}
        except Exception as e: logger.debug(f"[Quiz parse] {e}")
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 and parts[1].strip() else random.choice(QUIZ_TOPICS)
        cid   = str(u.effective_chat.id); cid_i = u.effective_chat.id
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
            except Exception as e: logger.error(f"[Quiz/poll] {e}")
        now   = time.time()
        used  = {h for h,exp in quiz_cooldown.get(cid,{}).items() if exp > now}
        avail = [fb for fb in FALLBACK_QS if q_hash(fb["q"]) not in used] or FALLBACK_QS
        fb    = random.choice(avail); mark_quiz(cid, fb["q"])
        pm = await c.bot.send_poll(
            chat_id=cid_i, question=f"🐱 {fb['q']}", options=fb["opts"],
            type="quiz", correct_option_id=fb["ans"], is_anonymous=False, explanation=fb["fact"])
        active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":fb["ans"],"topic":topic}
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[quiz] {e}", exc_info=True); bot_status["error_count"] += 1
        try: await u.message.reply_text("😿 Quiz failed!")
        except Exception: pass

async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        ans  = u.poll_answer
        if not ans: return
        info = active_polls.get(ans.poll_id)
        if not info or not ans.option_ids: return
        if ans.option_ids[0] != info["correct_index"]: return
        cid  = str(info["chat_id"]); uid = str(ans.user.id)
        name = (ans.user.first_name or "?")[:30]
        new_score = update_score(cid, uid, name, +10)
        asyncio.create_task(async_github_save())
        logger.info(f"[Score] +10 {name} (uid={uid}) = {new_score} pts in chat {cid}")
    except Exception as e: logger.debug(f"[poll_answer] {e}")

# ══════════════════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════════════════
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid    = str(u.effective_chat.id)
        scores = db.get("scores",{}).get(cid,{})
        if not scores:
            await u.message.reply_text(
                "📊 No scores yet! Play `/quiz` to earn points 🐾",
                parse_mode=ParseMode.MARKDOWN); return
        board = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        lines = ["╔════════════════════════════╗",
                 "🏆  *QUIZ LEADERBOARD*  🏆",
                 "╚════════════════════════════╝\n"]
        for i, e in enumerate(board[:10]):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
            lines.append(f"{medal} {e['name'][:18]:<18} —  *{e['score']:,} pts*")
        lines += ["\n━━━━━━━━━━━━━━━━━━━━",
                  "📈 Sorted: Highest → Lowest",
                  "━━━━━━━━━━━━━━━━━━━━",
                  "_+10 quiz  |  +20 games_"]
        await u.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[lb] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  /pump  /dump
# ══════════════════════════════════════════════════════
async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner-only command."); return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text(
                "⚠️ Reply to a user's message.\nExample: reply + `/pump 80000`",
                parse_mode=ParseMode.MARKDOWN); return
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await u.message.reply_text("⚠️ Usage: `/pump 80000` or `/dump 80000`",
                parse_mode=ParseMode.MARKDOWN); return
        amount = int(parts[1])
        cmd    = parts[0].lstrip("/").lower().split("@")[0]
        delta  = +amount if cmd == "pump" else -amount
        target = u.message.reply_to_message.from_user
        cid    = str(u.effective_chat.id)
        new_sc = update_score(cid, str(target.id), (target.first_name or "User")[:30], delta)
        asyncio.create_task(async_github_save())
        sign   = "+" if delta > 0 else ""
        emoji  = "🚀" if cmd == "pump" else "📉"
        await u.message.reply_text(
            f"{emoji} *{'PUMP' if cmd=='pump' else 'DUMP'}*\n\n"
            f"👤 *{target.first_name}*\n"
            f"🪪 User ID: `{target.id}`\n"
            f"{'📈' if delta>0 else '📉'} {sign}{amount:,} pts\n"
            f"💰 New Total: *{new_sc:,} pts*",
            parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[pump_dump] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  GAME HELPERS
# ══════════════════════════════════════════════════════
def register_player(uid: str, gkey: str):
    user_in_game[uid] = gkey

def release_player(uid: str):
    user_in_game.pop(uid, None)

def player_busy(uid: str) -> bool:
    gkey = user_in_game.get(uid)
    if not gkey: return False
    if gkey in ttt_games or gkey in rps_games: return True
    release_player(uid); return False

async def cleanup_expired_games():
    now = time.time()
    for gkey in list(ttt_games.keys()):
        g = ttt_games[gkey]
        if now - g.get("created", now) > GAME_TIMEOUT:
            release_player(str(g.get("x_id",""))); release_player(str(g.get("o_id","")))
            game_timers.pop(gkey, None); del ttt_games[gkey]
    for gkey in list(rps_games.keys()):
        g = rps_games[gkey]
        if now - g.get("created", now) > GAME_TIMEOUT:
            release_player(str(g.get("p1_id",""))); release_player(str(g.get("p2_id","")))
            del rps_games[gkey]

# ══════════════════════════════════════════════════════
#  TIMER TASK for TTT
# ══════════════════════════════════════════════════════
async def run_game_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    """Counts down 60s. Updates board text every 5s. Ends game on timeout."""
    try:
        while True:
            await asyncio.sleep(5)
            g = ttt_games.get(gkey)
            td = game_timers.get(gkey)
            if not g or not td: return
            if g.get("status") != "playing": return

            td["remaining"] = max(0, td["remaining"] - 5)

            cid    = g["chat_id"]
            msg_id = g.get("msg_id")
            if not msg_id: return

            if td["remaining"] <= 0:
                # Timeout — other player wins
                winner_name = g["x_name"] if g["turn"] == "O" else g["o_name"]
                winner_uid  = str(g["x_id"]) if g["turn"] == "O" else str(g["o_id"])
                g["status"]      = "timeout"
                g["winner_name"] = winner_name
                try:
                    await c.bot.edit_message_text(
                        chat_id=cid, message_id=msg_id,
                        text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(g["board"], disabled=True))
                except Exception: pass
                if not g["vs_bot"]:
                    update_score(str(cid), winner_uid, winner_name, +20)
                    asyncio.create_task(async_github_save())
                release_player(str(g["x_id"])); release_player(str(g["o_id"]))
                game_timers.pop(gkey, None); ttt_games.pop(gkey, None)
                return
            else:
                # Just update timer display
                try:
                    await c.bot.edit_message_text(
                        chat_id=cid, message_id=msg_id,
                        text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(g["board"]))
                except Exception: pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"[Timer] {e}")

# ══════════════════════════════════════════════════════
#  TIC TAC TOE
# ══════════════════════════════════════════════════════
TTT_EMPTY = "⬜"; TTT_X = "❌"; TTT_O = "⭕"
WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

def ttt_check_winner(board: list) -> Optional[str]:
    for a,b,cc in WINS:
        if board[a] == board[b] == board[cc] and board[a] != TTT_EMPTY:
            return board[a]
    return None

def ttt_is_draw(board: list) -> bool:
    return all(c != TTT_EMPTY for c in board) and not ttt_check_winner(board)

def ttt_bot_move(board: list) -> int:
    for i in range(9):
        if board[i] == TTT_EMPTY:
            board[i] = TTT_O
            if ttt_check_winner(board): board[i] = TTT_EMPTY; return i
            board[i] = TTT_EMPTY
    for i in range(9):
        if board[i] == TTT_EMPTY:
            board[i] = TTT_X
            if ttt_check_winner(board): board[i] = TTT_EMPTY; return i
            board[i] = TTT_EMPTY
    if board[4] == TTT_EMPTY: return 4
    for i in [0,2,6,8]:
        if board[i] == TTT_EMPTY: return i
    for i in range(9):
        if board[i] == TTT_EMPTY: return i
    return -1

def ttt_build_keyboard(board: list, disabled: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx  = row*3 + col
            cell = board[idx]
            label = cell if cell != TTT_EMPTY else TTT_EMPTY
            cb = f"ttt:noop:{idx}" if (cell != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            r.append(InlineKeyboardButton(label, callback_data=cb))
        rows.append(r)
    return InlineKeyboardMarkup(rows)

def ttt_build_text(g: dict) -> str:
    x_name = g["x_name"]; o_name = g["o_name"]
    turn   = g["turn"];    status = g.get("status","playing")
    gkey   = f"{g['chat_id']}:{g.get('msg_id','')}"
    td     = game_timers.get(gkey, {})
    rem    = td.get("remaining", TIMER_DURATION)
    tsec   = f"{rem//60:02d}:{rem%60:02d}"

    board = g["board"]
    rows  = [" ".join(board[r*3+col] for col in range(3)) for r in range(3)]
    board_str = "\n".join(rows)

    if status == "playing":
        cur = x_name if turn == "X" else o_name
        sym = TTT_X  if turn == "X" else TTT_O
        sl  = f"🎯 *{cur}'s Turn* {sym}\n⏱ `{tsec}` remaining"
    elif status == "timeout":
        sl = f"⏰ *Time Up!*\n🏆 *{g.get('winner_name','')}* wins by timeout!  +20 pts"
    elif status == "draw":
        sl = "🤝 *Match Draw!*"
    else:
        sl = f"🏆 *{g.get('winner_name','')} Wins!*  🎁 +20 pts"

    return (
        f"🎮 *TIC TAC TOE*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ *{x_name}*   🆚   *{o_name}* ⭕\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{board_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{sl}"
    )

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games()
        ua    = u.effective_user; cid = u.effective_chat.id
        uid_a = str(ua.id);  name_a = (ua.first_name or "Player")[:20]
        vs_bot = True; user_b_id = None; name_b = "🤖 Bot"

        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot = False; user_b_id = rb.id
                name_b = (rb.first_name or "Player2")[:20]
                if player_busy(str(rb.id)):
                    await u.message.reply_text("⚠️ That player is already in a game!"); return

        if player_busy(uid_a):
            await u.message.reply_text("⚠️ You're already in a game! Finish it first."); return

        board = [TTT_EMPTY] * 9
        g = {"board":board,"turn":"X","x_id":ua.id,"x_name":name_a,
             "o_id":user_b_id if not vs_bot else -1,"o_name":name_b,
             "vs_bot":vs_bot,"status":"playing","created":time.time(),
             "chat_id":cid,"msg_id":None}

        msg  = await u.message.reply_text(ttt_build_text(g),
            parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        g["msg_id"] = msg.message_id
        gkey = game_key(msg.message_id, cid)
        ttt_games[gkey] = g
        game_timers[gkey] = {"remaining": TIMER_DURATION}
        register_player(uid_a, gkey)
        if not vs_bot: register_player(str(user_b_id), gkey)
        asyncio.create_task(run_game_timer(c, gkey))
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[tictac] {e}", exc_info=True)

async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        parts = q.data.split(":")
        if len(parts) != 3 or parts[0] != "ttt": return
        action = parts[1]; idx = int(parts[2])
        cid    = q.message.chat_id; mid = q.message.message_id
        gkey   = game_key(mid, cid); g = ttt_games.get(gkey)

        if not g: await q.answer("⏰ Game expired.", show_alert=True); return
        if g["status"] != "playing": await q.answer("Game ended!", show_alert=True); return
        if action == "noop": await q.answer("Cell taken!", show_alert=True); return

        uid     = str(q.from_user.id)
        valid_x = uid == str(g["x_id"])
        valid_o = uid == str(g["o_id"]) or (g["vs_bot"] and valid_x)
        is_participant = uid in [str(g["x_id"]), str(g["o_id"])]

        if g["turn"] == "X" and not valid_x:
            await q.answer("❌ You are not part of this game!" if not is_participant else "Not your turn!", show_alert=True); return
        if g["turn"] == "O" and not g["vs_bot"] and not valid_o:
            await q.answer("❌ You are not part of this game!" if not is_participant else "Not your turn!", show_alert=True); return

        # Reset timer on move
        if gkey in game_timers:
            game_timers[gkey]["remaining"] = TIMER_DURATION

        board = g["board"]
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        ws = ttt_check_winner(board)

        def _end(winner_sym=None):
            if winner_sym:
                wname = g["x_name"] if winner_sym == TTT_X else g["o_name"]
                wuid  = str(g["x_id"]) if winner_sym == TTT_X else str(g["o_id"])
                g["status"] = "win"; g["winner_name"] = wname
                if not g["vs_bot"] or winner_sym == TTT_X:
                    update_score(str(cid), wuid, wname, +20)
                    asyncio.create_task(async_github_save())
            else:
                g["status"] = "draw"
            game_timers.pop(gkey, None)
            release_player(str(g["x_id"])); release_player(str(g["o_id"]))
            ttt_games.pop(gkey, None)

        if ws:
            _end(ws)
            await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                reply_markup=ttt_build_keyboard(board, disabled=True)); return
        if ttt_is_draw(board):
            _end(); await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                reply_markup=ttt_build_keyboard(board, disabled=True)); return

        g["turn"] = "O" if g["turn"] == "X" else "X"

        if g["vs_bot"] and g["turn"] == "O":
            bi = ttt_bot_move(board)
            if bi >= 0:
                board[bi] = TTT_O; ws2 = ttt_check_winner(board)
                if ws2:
                    _end(ws2)
                    await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(board, disabled=True)); return
                if ttt_is_draw(board):
                    _end(); await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(board, disabled=True)); return
                g["turn"] = "X"

        await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
            reply_markup=ttt_build_keyboard(board))
    except Exception as e: logger.error(f"[ttt_cb] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  ROCK PAPER SCISSORS
# ══════════════════════════════════════════════════════
RPS_BEATS = {"rock":"scissors","scissors":"paper","paper":"rock"}
RPS_EMOJI = {"rock":"🪨","paper":"📄","scissors":"✂️"}

def rps_keyboard(gkey: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟥🪨 Rock",     callback_data=f"rps:{gkey}:rock"),
        InlineKeyboardButton("🟦📄 Paper",    callback_data=f"rps:{gkey}:paper"),
        InlineKeyboardButton("🟨✂️ Scissors", callback_data=f"rps:{gkey}:scissors"),
    ]])

def rps_build_text(g: dict) -> str:
    p1   = g["p1_name"]; p2 = g["p2_name"]
    c1   = g.get("p1_choice"); c2 = g.get("p2_choice")
    status = g.get("status","waiting")
    p1l  = f"✅ *{p1}* — Locked 🔒" if c1 else f"⌛ *{p1}* — Choosing…"
    p2l  = f"✅ *{p2}* — Locked 🔒" if c2 else f"⌛ *{p2}* — Choosing…"
    if status == "done":
        e1 = RPS_EMOJI.get(c1,"?"); e2 = RPS_EMOJI.get(c2,"?")
        winner = g.get("winner","draw"); wn = g.get("winner_name","")
        result = (f"👤 *{p1}*: {e1} {c1}\n👤 *{p2}*: {e2} {c2}\n"
                  f"━━━━━━━━━━━━━━━\n"
                  + (f"🤝 *Draw!*" if winner=="draw" else f"👑 *{wn} Wins!*  🎁 +20 pts"))
        return f"🪨📄✂️ *ROCK • PAPER • SCISSORS*\n━━━━━━━━━━━━━━━\n{result}"
    return (f"🎮 *ROCK • PAPER • SCISSORS*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"⚔️  *{p1}*   🆚   *{p2}*\n━━━━━━━━━━━━━━━━━━━━\n\n{p1l}\n{p2l}")

async def rock_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games()
        ua    = u.effective_user; cid = u.effective_chat.id
        uid_a = str(ua.id);  name_a = (ua.first_name or "Player")[:20]
        vs_bot = True; uid_b = None; name_b = "🤖 Bot"

        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot = False; uid_b = str(rb.id)
                name_b = (rb.first_name or "Player2")[:20]
                if player_busy(uid_b):
                    await u.message.reply_text("⚠️ That player is already in a game!"); return

        if player_busy(uid_a):
            await u.message.reply_text("⚠️ You're already in a game!"); return

        g = {"p1_id":ua.id,"p1_name":name_a,"p2_id":int(uid_b) if uid_b else -1,
             "p2_name":name_b,"p1_choice":None,"p2_choice":None,
             "vs_bot":vs_bot,"status":"waiting","created":time.time(),"chat_id":cid}

        msg = await u.message.reply_text(rps_build_text(g),
            parse_mode=ParseMode.MARKDOWN, reply_markup=rps_keyboard("tmp"))
        gkey = game_key(msg.message_id, cid)
        rps_games[gkey] = g
        register_player(uid_a, gkey)
        if not vs_bot and uid_b: register_player(uid_b, gkey)
        try: await msg.edit_reply_markup(rps_keyboard(gkey))
        except Exception: pass
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[rock] {e}", exc_info=True)

async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        parts = q.data.split(":")
        if len(parts) != 3 or parts[0] != "rps": return
        _, raw_gkey, choice = parts
        # gkey is "cid:mid" but split by ":" gives 3 parts total — reconstruct
        cid    = q.message.chat_id; mid = q.message.message_id
        gkey   = game_key(mid, cid)
        g      = rps_games.get(gkey)

        if not g: await q.answer("⏰ Game expired.", show_alert=True); return
        if g["status"] == "done": await q.answer("Game ended!", show_alert=True); return

        uid  = str(q.from_user.id)
        is_p1 = uid == str(g["p1_id"])
        is_p2 = uid == str(g["p2_id"]) or (g["vs_bot"] and is_p1)

        if not is_p1 and not is_p2:
            await q.answer("❌ You are not part of this game!", show_alert=True); return

        if is_p1 and not g["p1_choice"]:
            g["p1_choice"] = choice
            if g["vs_bot"]:
                g["p2_choice"] = random.choice(list(RPS_BEATS.keys()))
            await q.answer("✅ Choice locked!")
        elif not is_p1 and is_p2 and not g["p2_choice"]:
            g["p2_choice"] = choice
            await q.answer("✅ Choice locked!")
        elif (is_p1 and g["p1_choice"]) or (not is_p1 and g["p2_choice"]):
            await q.answer("You already chose!", show_alert=True); return

        if g["p1_choice"] and g["p2_choice"]:
            c1 = g["p1_choice"]; c2 = g["p2_choice"]
            if c1 == c2:
                g["winner"] = "draw"; g["winner_name"] = ""
            elif RPS_BEATS.get(c1) == c2:
                g["winner"] = "p1"; g["winner_name"] = g["p1_name"]
                update_score(str(cid), str(g["p1_id"]), g["p1_name"], +20)
                asyncio.create_task(async_github_save())
            else:
                g["winner"] = "p2"; g["winner_name"] = g["p2_name"]
                if not g["vs_bot"]:
                    update_score(str(cid), str(g["p2_id"]), g["p2_name"], +20)
                    asyncio.create_task(async_github_save())
            g["status"] = "done"
            await q.edit_message_text(rps_build_text(g), parse_mode=ParseMode.MARKDOWN)
            release_player(str(g["p1_id"])); release_player(str(g["p2_id"]))
            del rps_games[gkey]
        else:
            await q.edit_message_text(rps_build_text(g),
                parse_mode=ParseMode.MARKDOWN, reply_markup=rps_keyboard(gkey))
    except Exception as e: logger.error(f"[rps_cb] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  FUN COMMANDS
# ══════════════════════════════════════════════════════
GAY_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *ATTENTION* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nAfter deep investigation:\n👉 *{u}* 👈\nis 🌈✨ *SUPER GAY* ✨🌈\nMust slay forever 💅😭\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n📡 *GOVERNMENT ALERT* 📡\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nRainbow activity from:\n👉 *{u}* 👈\n🌈 *Certified Gay Citizen* 🌈\nToo fabulous! 😭✨\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
]
COUPLE_T = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n💘 *LOVE DETECTOR 3000* 💘\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👉 *{u1}* ❤️ *{u2}* 👈\nCompatibility: ██████████ 100%\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n🚨 *COUPLE ALERT* 🚨\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👉 *{u1}* 💞 *{u2}* 👈\n\n💖 *OFFICIAL COUPLE* 💖\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid   = str(u.effective_chat.id)
        cmd   = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        users = list(db.get("seen",{}).get(cid,{}).values())
        if len(users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text("Meow… need more members! 😿🐾"); return
        day  = datetime.now().strftime("%y-%m-%d"); lk = f"{cid}:{cmd}"
        if lk in db.get("locks",{}) and db["locks"][lk]["date"] == day:
            res = db["locks"][lk]["res"]
        else:
            if cmd == "couple":
                m   = random.sample(users, 2)
                res = random.choice(COUPLE_T).format(u1=m[0]["n"], u2=m[1]["n"])
            else:
                m   = [random.choice(users)]
                res = random.choice(GAY_T).format(u=m[0]["n"])
            db.setdefault("locks",{})[lk] = {"date": day, "res": res}; save_db()
        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[fun] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  /start  /help
# ══════════════════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        ocmds = "\n`/pump` `/dump` — Owner adjust points" if OWNER_ID else ""
        text = (
            "🧠 *BELUGA BOT*  v6.0\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🎮 *Games*\n"
            "`/tictac` — Tic Tac Toe (reply = PvP)\n"
            "`/rock` — Rock Paper Scissors (reply = PvP)\n\n"
            "🎓 *Utilities*\n"
            "`/search <topic>` — AI Smart Search\n"
            "`/quiz` — Trivia  |  `/quiz crypto` — Topic quiz\n"
            "`/lb` — Leaderboard 🏆\n\n"
            "🎉 *Fun*\n"
            "`/gay`  `/couple` — Daily fun\n\n"
            "🤖 *Auto*\n"
            "• Mention *beluga* — AI chat\n"
            "• Send YT/Instagram link → auto download\n"
            f"{ocmds}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔥 _Ready? Start chatting!_"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[start] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  MONITOR
# ══════════════════════════════════════════════════════
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

        # ── Auto media download ────────────────────────────
        media_m = _MEDIA_RE.search(text)
        if media_m:
            asyncio.create_task(download_and_send(u, c, media_m.group(0)))

        # ── AI chat ───────────────────────────────────────
        beluga   = "beluga" in text_low
        reply_me = (u.message.reply_to_message and
                    u.message.reply_to_message.from_user and
                    u.message.reply_to_message.from_user.id == c.bot.id)
        mention  = any("beluga" in text_low[e.offset:e.offset+e.length]
                       for e in (u.message.entities or []) if e.type == "mention")
        if beluga or reply_me or mention:
            try:
                await c.bot.send_chat_action(u.effective_chat.id, "typing")
                emoji = await ai_emoji(text)
                await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
                reply = await ai(CHAT_PROMPT, text, "Meow! 🐾")
                await u.message.reply_text(reply)
            except Exception as e: logger.error(f"[monitor/chat] {e}", exc_info=True)

        bot_status["message_count"] += 1
        bot_status["last_update"] = datetime.now()
    except Exception as e:
        logger.error(f"[monitor] {e}", exc_info=True); bot_status["error_count"] += 1

# ══════════════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)): logger.debug(f"[Net] {type(err).__name__}"); return
    if isinstance(err, RetryAfter):
        logger.warning(f"[RateLimit] {err.retry_after}s"); await asyncio.sleep(err.retry_after+1); return
    if isinstance(err, (Forbidden, BadRequest)): logger.debug(f"[{type(err).__name__}] {err}"); return
    if isinstance(err, InvalidToken): logger.critical("❌ TOKEN REJECTED"); bot_status["running"]=False; return
    logger.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))
    bot_status["error_count"] += 1

# ══════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════
async def main():
    logger.info("="*55)
    logger.info("🐱  BELUGA BOT  v6.0.0")
    logger.info(f"   PORT={HTTP_PORT}  OWNER_ID={OWNER_ID}")
    logger.info(f"   GITHUB_TOKEN={'set ✅' if GITHUB_TOKEN else 'NOT SET ❌'}")
    logger.info(f"   GITHUB_GIST_ID='{GITHUB_GIST or '(will auto-create)'}'")
    logger.info("="*55)

    # 1. HTTP first
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    # 2. Load scores from GitHub
    loop = asyncio.get_running_loop()
    if GITHUB_TOKEN:
        try:
            gh = await asyncio.wait_for(
                loop.run_in_executor(None, github_load_scores), timeout=20)
            if gh:
                db["scores"] = gh; save_db()
                logger.info(f"✅ Loaded {sum(len(v) for v in gh.values())} user scores")
            else:
                logger.info("[GitHub] No scores yet — will save on first update")
        except Exception as e:
            logger.warning(f"[GitHub startup] {e}")
    else:
        logger.warning("⚠️  GITHUB_TOKEN not set — add it to Render env vars for persistence!")

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
    logger.info("✅ All handlers registered")

    # 4. Start polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    bot_status["running"] = True
    logger.info("✅ Beluga v6.0.0 is LIVE 🐱")

    # 5. Keep alive
    stop_evt = asyncio.Event()
    try:
        import signal
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT,  stop_evt.set)
    except (NotImplementedError, RuntimeError): pass

    async def periodic():
        while not stop_evt.is_set():
            await asyncio.sleep(60)
            await cleanup_expired_games()

    cleanup_task = asyncio.create_task(periodic())
    try:
        await stop_evt.wait()
    except (KeyboardInterrupt, asyncio.CancelledError): pass

    # 6. Shutdown
    cleanup_task.cancel()
    bot_status["running"] = False
    logger.info("🔄 Saving to GitHub before shutdown…")
    if GITHUB_TOKEN:
        try: await loop.run_in_executor(None, github_save_scores)
        except Exception: pass
    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try: await fn()
        except Exception: pass
    logger.info("✅ Shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt: logger.info("👋 Bye!")
    except InvalidToken: logger.critical("❌ Invalid BOT_TOKEN"); sys.exit(1)
    except Exception as e: logger.critical(f"❌ FATAL: {e}", exc_info=True); sys.exit(1)
