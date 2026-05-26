# ═══════════════════════════════════════════════════════════════
#  BELUGA BOT  v8.0.0
#  Supabase: replaces GitHub gist for persistence
#  yt-dlp: YouTube + Instagram auto-download on link detection
#  /tictac PvP + vs Bot with timer
#  /quiz /lb /pump /dump /search /gay /couple
#  /health /ping always 200
#  /rock REMOVED
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
DATA_FILE      = "beluga_brain.json"
OR_KEY         = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
HTTP_PORT      = int(os.environ.get("PORT", "10000"))
OWNER_ID       = int(os.environ.get("OWNER_ID", "0"))
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")

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
user_in_game:  dict[str, str]              = {}
game_timers:   dict[str, dict]             = {}
GAME_TIMEOUT   = 300
TIMER_DURATION = 60
_dl_tracker:   dict[str, list]             = {}

LB_PHOTO_URL = "https://i.postimg.cc/FKN1C157/file-00000000bce4720b905dc2e04c58fa80.png"

# ══════════════════════════════════════════════════════
#  DATABASE (local JSON — fallback/cache)
# ══════════════════════════════════════════════════════
def load_db() -> dict:
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"[DB Load] {e}")
    return {"seen": {}, "locks": {}, "counts": {}, "scores": {}, "weekly_winners": {}}

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
db.setdefault("weekly_winners", {})

# ══════════════════════════════════════════════════════
#  SUPABASE HELPERS
#
#  Tables required (run in Supabase SQL editor):
#
#  CREATE TABLE IF NOT EXISTS users (
#    id          BIGSERIAL PRIMARY KEY,
#    chat_id     TEXT NOT NULL,
#    user_id     TEXT NOT NULL,
#    name        TEXT NOT NULL DEFAULT '',
#    score       INTEGER NOT NULL DEFAULT 0,
#    updated_at  TIMESTAMPTZ DEFAULT NOW(),
#    UNIQUE(chat_id, user_id)
#  );
#
#  CREATE TABLE IF NOT EXISTS weekly_winners (
#    id          BIGSERIAL PRIMARY KEY,
#    chat_id     TEXT NOT NULL UNIQUE,
#    top3        JSONB NOT NULL DEFAULT '[]',
#    week_label  TEXT NOT NULL DEFAULT '',
#    reset_at    TIMESTAMPTZ DEFAULT NOW()
#  );
# ══════════════════════════════════════════════════════

def _sb_headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }

def _sb_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"

def supabase_load_scores() -> dict:
    """Load all user scores from Supabase → dict[chat_id][user_id]={name,score,user_id}"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("[Supabase] Credentials not set — running without persistence")
        return {}
    try:
        r = requests.get(
            _sb_url("users"),
            headers=_sb_headers(),
            params={"select": "chat_id,user_id,name,score"},
            timeout=15,
        )
        if r.status_code != 200:
            logger.error(f"[Supabase Load Scores] status={r.status_code} {r.text[:200]}")
            return {}
        rows = r.json()
        out: dict = {}
        for row in rows:
            cid = row["chat_id"]; uid = row["user_id"]
            out.setdefault(cid, {})[uid] = {
                "name":    row["name"],
                "user_id": int(uid) if uid.lstrip("-").isdigit() else 0,
                "score":   row["score"],
            }
        logger.info(f"✅ Loaded scores from Supabase ({len(rows)} rows, {len(out)} chats)")
        return out
    except Exception as e:
        logger.error(f"[Supabase Load Scores] {e}")
        return {}

def supabase_upsert_score(chat_id: str, user_id: str, name: str, score: int) -> bool:
    """Upsert a single user's score into Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        payload = {
            "chat_id":    chat_id,
            "user_id":    user_id,
            "name":       name,
            "score":      score,
            "updated_at": datetime.utcnow().isoformat(),
        }
        hdrs = {**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        r = requests.post(_sb_url("users"), headers=hdrs, json=payload, timeout=10)
        if r.status_code in (200, 201):
            return True
        logger.warning(f"[Supabase Upsert Score] status={r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"[Supabase Upsert Score] {e}")
    return False

def supabase_load_weekly_winners() -> dict:
    """Load weekly_winners table → dict[chat_id]={top3, week_label, reset_at}"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    try:
        r = requests.get(
            _sb_url("weekly_winners"),
            headers=_sb_headers(),
            params={"select": "chat_id,top3,week_label,reset_at"},
            timeout=15,
        )
        if r.status_code != 200:
            logger.error(f"[Supabase Load Winners] status={r.status_code} {r.text[:200]}")
            return {}
        rows = r.json()
        out = {}
        for row in rows:
            out[row["chat_id"]] = {
                "top3":       row.get("top3", []),
                "week_label": row.get("week_label", ""),
                "reset_at":   row.get("reset_at", ""),
            }
        logger.info(f"✅ Loaded weekly winners ({len(out)} chats)")
        return out
    except Exception as e:
        logger.error(f"[Supabase Load Winners] {e}")
        return {}

def supabase_save_weekly_winners(chat_id: str, top3: list, week_label: str) -> bool:
    """Upsert weekly winners for a chat."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        payload = {
            "chat_id":    chat_id,
            "top3":       top3,
            "week_label": week_label,
            "reset_at":   datetime.utcnow().isoformat(),
        }
        hdrs = {**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        r = requests.post(_sb_url("weekly_winners"), headers=hdrs, json=payload, timeout=10)
        if r.status_code in (200, 201):
            return True
        logger.warning(f"[Supabase Save Winners] status={r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"[Supabase Save Winners] {e}")
    return False

def supabase_reset_scores(chat_id: str) -> bool:
    """Set all scores in a chat to 0."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        r = requests.patch(
            _sb_url("users"),
            headers=_sb_headers(),
            params={"chat_id": f"eq.{chat_id}"},
            json={"score": 0, "updated_at": datetime.utcnow().isoformat()},
            timeout=10,
        )
        if r.status_code in (200, 204):
            return True
        logger.warning(f"[Supabase Reset Scores] status={r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"[Supabase Reset Scores] {e}")
    return False

async def async_sb_upsert(chat_id: str, user_id: str, name: str, score: int):
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, supabase_upsert_score, chat_id, user_id, name, score)
    except Exception as e:
        logger.debug(f"[Async SB Upsert] {e}")

def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {
        "name":    name,
        "user_id": int(uid) if uid.lstrip("-").isdigit() else 0,
        "score":   0,
    })
    e["name"]    = name
    e["user_id"] = int(uid) if uid.lstrip("-").isdigit() else 0
    e["score"]   = max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    save_db()
    asyncio.create_task(async_sb_upsert(cid, uid, name, e["score"]))
    return e["score"]

def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

# ══════════════════════════════════════════════════════
#  HTTP SERVER — always 200
# ══════════════════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({
        "status":    "healthy",
        "uptime_seconds": up,
        "running":   bot_status["running"],
        "messages":  bot_status["message_count"],
        "errors":    bot_status["error_count"],
        "supabase":  "configured" if SUPABASE_URL else "not configured",
        "version":   "8.0.0",
        "ts":        datetime.now().isoformat(),
    }, status=200)

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()}, status=200)

async def _stats(req):
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    ok = bot_status["api_calls"] - bot_status["failed_apis"]
    return web.json_response({
        "uptime_hours":    round(up/3600, 2),
        "messages":        bot_status["message_count"],
        "errors":          bot_status["error_count"],
        "api_calls":       bot_status["api_calls"],
        "success_rate_pct": round(ok/max(bot_status["api_calls"],1)*100, 2),
        "active_ttt_games": len(ttt_games),
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
WIKI_UA = {"User-Agent": "BelugaBot/8.0"}
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
#  MEDIA DOWNLOAD — YouTube + Instagram + X
#  Uses yt-dlp (add yt-dlp to requirements.txt)
# ══════════════════════════════════════════════════════
_MEDIA_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:"
    r"(?:twitter\.com|x\.com)/\S+?/status/\d+"
    r"|instagram\.com/(?:p|reel|tv|stories)/[A-Za-z0-9_\-]+"
    r"|(?:youtu\.be/[A-Za-z0-9_\-]+|youtube\.com/(?:watch|shorts|embed)\S+)"
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
    """Download with yt-dlp. Returns {ok, path, type, title, error}."""
    result = {"ok": False, "path": None, "type": "video", "title": "", "error": ""}
    try:
        import yt_dlp
    except ImportError:
        result["error"] = "yt_dlp not installed"
        logger.error("yt_dlp not installed — add yt-dlp to requirements.txt")
        return result

    # ── Cookie approach for age-restricted / login-required content ──
    # If you have a cookies.txt file exported from your browser, set path here.
    cookies_file = os.environ.get("YT_COOKIES_FILE", "")

    ydl_opts = {
        # Priority: pre-muxed mp4 ≤ 720p (no ffmpeg = fast), then merge, then any
        "format": (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/best[ext=mp4][height<=720][filesize<49M]"
            "/best[ext=mp4][filesize<49M]"
            "/bestvideo[ext=mp4]+bestaudio"
            "/best[filesize<49M]"
        ),
        "outtmpl":            os.path.join(outdir, "media.%(ext)s"),
        "quiet":              True,
        "no_warnings":        True,
        "noplaylist":         True,
        "max_filesize":       49 * 1024 * 1024,
        "socket_timeout":     30,
        "retries":            3,
        "fragment_retries":   3,
        "merge_output_format":"mp4",
        "concurrent_fragment_downloads": 4,
        # Keep ffmpeg merge enabled — required for bestvideo+bestaudio
        "postprocessors": [{
            "key":            "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; SM-S906N Build/QP1A.190711.020; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                "Chrome/80.0.3987.119 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            },
            "instagram": {"api": ["android"]},
        },
    }

    # Attach cookies if available
    if cookies_file and os.path.exists(cookies_file):
        ydl_opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                result["error"] = "No info extracted"; return result

            result["title"] = (info.get("title") or info.get("description") or "")[:80].strip()

            # Find the downloaded file
            found = None
            for ext in [".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi",
                        ".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                candidate = os.path.join(outdir, f"media{ext}")
                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                    found = candidate; break

            if not found:
                files = sorted(
                    [f for f in os.listdir(outdir) if not f.endswith((".part", ".ytdl"))],
                    key=lambda f: os.path.getsize(os.path.join(outdir, f)),
                    reverse=True,
                )
                if files:
                    found = os.path.join(outdir, files[0])

            if not found:
                result["error"] = "No file after download"; return result

            sz = os.path.getsize(found)
            if sz == 0:             result["error"] = "Empty file"; return result
            if sz > 50*1024*1024:   result["error"] = f"Too large ({sz//1024//1024} MB)"; return result

            ext_l = os.path.splitext(found)[1].lower()
            ftype  = "image" if ext_l in (".jpg",".jpeg",".png",".gif",".webp") else "video"
            result.update({"ok": True, "path": found, "type": ftype})

    except Exception as e:
        err_s = str(e).lower()
        if any(x in err_s for x in [
            "private","login required","age","unavailable","removed",
            "suspended","not found","403","404","not available",
            "this video is not available","members-only","requires authentication",
            "this post may have been removed","sign in",
        ]):
            result["error"] = "unavailable"
        else:
            result["error"] = str(e)[:200]
            logger.debug(f"[yt-dlp] {e}")

    return result

async def download_and_send(u: Update, c: ContextTypes.DEFAULT_TYPE, url: str):
    cid = u.effective_chat.id
    if not _dl_rate_ok(str(cid)):
        return  # silent rate limit

    url_l = url.lower()
    if "youtu"       in url_l: platform, pemoji = "YouTube",    "▶️"
    elif "instagram" in url_l: platform, pemoji = "Instagram",  "📸"
    else:                      platform, pemoji = "X (Twitter)", "🐦"

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
            timeout=120.0)

        if status_msg:
            try: await status_msg.delete()
            except Exception: pass

        if not result["ok"]:
            if result["error"] == "unavailable":
                await safe_react(c.bot, cid, u.message.message_id, "🔒")
            else:
                logger.debug(f"[DL fail] {result['error']}")
                try:
                    await u.message.reply_text(
                        f"⚠️ _Could not download:_ `{result['error'][:100]}`",
                        parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
            return

        caption = f"{pemoji} *Downloaded from {platform}*"
        if result["title"] and result["title"].lower() not in ("","video","media","reel"):
            caption += f"\n_{result['title'][:100]}_"
        caption += "\n✅ _via Beluga Bot_"

        await c.bot.send_chat_action(
            cid, "upload_photo" if result["type"] == "image" else "upload_video")

        with open(result["path"], "rb") as f:
            if result["type"] == "image":
                await u.message.reply_photo(photo=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
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
            try: await status_msg.edit_text("⚠️ Download failed — try again.")
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
        logger.info(f"[Score] +10 {name} (uid={uid}) = {new_score} pts in chat {cid}")
    except Exception as e: logger.debug(f"[poll_answer] {e}")

# ══════════════════════════════════════════════════════
#  LEADERBOARD  — new design
# ══════════════════════════════════════════════════════
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

def _days_until_next_monday() -> int:
    """Days until next Monday (weekly reset day)."""
    today = datetime.now().weekday()  # 0=Mon
    return (7 - today) % 7 or 7

async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid    = str(u.effective_chat.id)
        scores = db.get("scores",{}).get(cid,{})
        ww     = db.get("weekly_winners",{}).get(cid)
        days   = _days_until_next_monday()

        lines = []

        # ── Hall of Fame ─────────────────────────────────────────
        lines.append("╭────────────────────────────────╮")
        lines.append("        🏆 *WEEKLY HALL OF FAME*")
        lines.append("╰────────────────────────────────╯")
        lines.append("")
        lines.append("       *PREVIOUS TOP 3 WEEK WINNERS*")
        lines.append("")

        if ww and ww.get("top3"):
            wk_label = ww.get("week_label","Last Week")
            lines.append(f"_Week: {wk_label}_")
            lines.append("")
            for i, e in enumerate(ww["top3"]):
                m = MEDALS[i]
                lines.append(f"  {m}  *{e['name'][:20]}*   —   {e['score']:,} pts")
        else:
            lines.append("  _(No previous week data yet)_")

        lines.append("")
        lines.append("╭────────────────────────────────╮")
        lines.append("          ⚡ *LIVE LEADERBOARD*")
        lines.append("╰────────────────────────────────╯")
        lines.append("")
        lines.append(f"⏳ *Weekly Reset In:* {days} Day{'s' if days != 1 else ''}")
        lines.append("")

        if not scores:
            for i in range(10):
                m = MEDALS[i]
                lines.append(f"{m}  `{'_'*19}`   0 pts")
        else:
            board = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
            for i in range(10):
                m = MEDALS[i]
                if i < len(board):
                    e    = board[i]
                    name = e["name"][:18]
                    pts  = e["score"]
                else:
                    name = "—"
                    pts  = 0
                lines.append(f"{m}  *{name:<18}*   {pts:,} pts")

        lines.append("")
        lines.append("📊 *Highest → Lowest*")
        lines.append("")
        lines.append("🎮 *Points System*")
        lines.append("➕ +10 Quiz / Game Win")
        lines.append("➖ \\-10 If Game Lost")

        caption = "\n".join(lines)

        # Send with the leaderboard photo
        try:
            await u.message.reply_photo(
                photo=LB_PHOTO_URL,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            # fallback: send as text if photo fails
            await u.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)

        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[lb] {e}", exc_info=True)
        try: await u.message.reply_text("😿 Leaderboard failed to load.")
        except Exception: pass

# ══════════════════════════════════════════════════════
#  /nw — New Week (owner only)
# ══════════════════════════════════════════════════════
async def nw_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner-only command."); return

        cid    = str(u.effective_chat.id)
        scores = db.get("scores",{}).get(cid,{})
        board  = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        top3   = [{"name": e["name"], "score": e["score"]} for e in board[:3]]
        wk_label = datetime.now().strftime("%d %b %Y")

        db.setdefault("weekly_winners",{})[cid] = {
            "top3":       top3,
            "week_label": wk_label,
            "reset_at":   datetime.now().isoformat(),
        }

        # Reset all scores in memory
        if cid in db.get("scores",{}):
            for uid_s in db["scores"][cid]:
                db["scores"][cid][uid_s]["score"] = 0
        save_db()

        # Persist to Supabase
        loop = asyncio.get_running_loop()
        asyncio.create_task(loop.run_in_executor(
            None, supabase_save_weekly_winners, cid, top3, wk_label))
        asyncio.create_task(loop.run_in_executor(
            None, supabase_reset_scores, cid))

        announce = [
            "🏆🎉 *NEW WEEK STARTED!* 🎉🏆",
            f"\n_Week ending {wk_label}_\n",
            "👑 *Last Week's Champions:*\n",
        ]
        if top3:
            for i, e in enumerate(top3):
                announce.append(f"{MEDALS[i]} *{e['name']}* — {e['score']:,} pts")
        else:
            announce.append("_(No scores recorded)_")

        announce += [
            "\n━━━━━━━━━━━━━━━━━━━━",
            "🔄 *All scores reset to 0*",
            "━━━━━━━━━━━━━━━━━━━━",
            "_New week, new battle! Play `/quiz` and `/tictac` to climb! 🚀_",
        ]
        await u.message.reply_text("\n".join(announce), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[nw] {e}", exc_info=True)
        try: await u.message.reply_text("😿 /nw failed!")
        except Exception: pass

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
    if gkey in ttt_games: return True
    release_player(uid); return False

async def cleanup_expired_games():
    now = time.time()
    for gkey in list(ttt_games.keys()):
        g = ttt_games[gkey]
        if now - g.get("created", now) > GAME_TIMEOUT:
            release_player(str(g.get("x_id",""))); release_player(str(g.get("o_id","")))
            game_timers.pop(gkey, None); del ttt_games[gkey]

# ══════════════════════════════════════════════════════
#  TIMER TASK for TTT
# ══════════════════════════════════════════════════════
async def run_game_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    try:
        while True:
            await asyncio.sleep(3)
            g  = ttt_games.get(gkey)
            td = game_timers.get(gkey)
            if not g or not td:
                return
            if g.get("status") != "playing":
                return

            td["remaining"] = max(0, td["remaining"] - 3)
            cid    = g["chat_id"]
            msg_id = g.get("msg_id")
            if not msg_id:
                return

            if td["remaining"] <= 0:
                if g["turn"] == "X":
                    winner_name = g["o_name"]; winner_uid = str(g["o_id"])
                    loser_name  = g["x_name"]; loser_uid  = str(g["x_id"])
                else:
                    winner_name = g["x_name"]; winner_uid = str(g["x_id"])
                    loser_name  = g["o_name"]; loser_uid  = str(g["o_id"])
                g["status"]      = "timeout"
                g["winner_name"] = winner_name
                try:
                    await c.bot.edit_message_text(
                        chat_id=cid, message_id=msg_id,
                        text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(g["board"], disabled=True))
                except Exception:
                    pass
                cid_s = str(cid)
                if not g["vs_bot"]:
                    update_score(cid_s, winner_uid, winner_name, +10)
                    update_score(cid_s, loser_uid,  loser_name,  -10)
                release_player(str(g["x_id"]))
                release_player(str(g["o_id"]))
                game_timers.pop(gkey, None)
                ttt_games.pop(gkey, None)
                return
            else:
                try:
                    await c.bot.edit_message_text(
                        chat_id=cid, message_id=msg_id,
                        text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(g["board"]))
                except Exception:
                    pass
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

def _minimax(board: list, is_maximizing: bool, alpha: int, beta: int) -> int:
    winner = ttt_check_winner(board)
    if winner == TTT_O: return 10
    if winner == TTT_X: return -10
    if all(c != TTT_EMPTY for c in board): return 0
    if is_maximizing:
        best = -100
        for i in range(9):
            if board[i] == TTT_EMPTY:
                board[i] = TTT_O
                score = _minimax(board, False, alpha, beta)
                board[i] = TTT_EMPTY
                best  = max(best, score)
                alpha = max(alpha, best)
                if beta <= alpha: break
        return best
    else:
        best = 100
        for i in range(9):
            if board[i] == TTT_EMPTY:
                board[i] = TTT_X
                score = _minimax(board, True, alpha, beta)
                board[i] = TTT_EMPTY
                best = min(best, score)
                beta = min(beta, best)
                if beta <= alpha: break
        return best

def ttt_bot_move(board: list) -> int:
    best_score = -1000
    best_move  = -1
    for i in range(9):
        if board[i] == TTT_EMPTY:
            board[i] = TTT_O
            score = _minimax(board, False, -1000, 1000)
            board[i] = TTT_EMPTY
            if score > best_score:
                best_score = score
                best_move  = i
    return best_move

def ttt_build_keyboard(board: list, disabled: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx  = row*3 + col
            cell = board[idx]
            cb = f"ttt:noop:{idx}" if (cell != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            r.append(InlineKeyboardButton(cell, callback_data=cb))
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
        sl = f"⏰ *Time Up!*\n🏆 *{g.get('winner_name','')}* wins by timeout!  +10 pts  📉 -10 pts"
    elif status == "draw":
        sl = "🤝 *Match Draw!*"
    else:
        sl = f"🏆 *{g.get('winner_name','')} Wins!*  🎁 +10 pts  📉 -10 pts"

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

        if gkey in game_timers:
            game_timers[gkey]["remaining"] = TIMER_DURATION

        board = g["board"]
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        ws = ttt_check_winner(board)

        def _end(winner_sym=None):
            if winner_sym:
                wname  = g["x_name"] if winner_sym == TTT_X else g["o_name"]
                wuid   = str(g["x_id"]) if winner_sym == TTT_X else str(g["o_id"])
                lname  = g["o_name"] if winner_sym == TTT_X else g["x_name"]
                luid   = str(g["o_id"]) if winner_sym == TTT_X else str(g["x_id"])
                g["status"] = "win"; g["winner_name"] = wname
                cid_s  = str(cid)
                if not g["vs_bot"]:
                    update_score(cid_s, wuid, wname, +10)
                    update_score(cid_s, luid, lname, -10)
                elif winner_sym == TTT_X:
                    update_score(cid_s, wuid, wname, +10)
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
#  /start  — eye-catching new design
# ══════════════════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        name = (u.effective_user.first_name or "human") if u.effective_user else "human"
        owner_section = ""
        if OWNER_ID:
            owner_section = (
                "\n┌─────────────────────────────┐\n"
                "│  🔐  *ADMIN CONTROLS*\n"
                "│  `/pump` `/dump` — Edit points\n"
                "│  `/nw` — Start new week + reset\n"
                "└─────────────────────────────┘\n"
            )
        text = (
            f"🐱✨ *Meow, {name}!* ✨🐱\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"         *BELUGA BOT v8.0*\n"
            f"    _Your sassy AI cat companion_\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"┌─────────────────────────────┐\n"
            f"│  🎮  *GAMES*\n"
            f"│  `/tictac` — Tic Tac Toe\n"
            f"│  ↳ _Reply a user for PvP_ ⚔️\n"
            f"│  ↳ _or play vs unbeatable bot_ 🤖\n"
            f"└─────────────────────────────┘\n\n"
            f"┌─────────────────────────────┐\n"
            f"│  🧠  *TRIVIA & SEARCH*\n"
            f"│  `/quiz` — Random trivia poll\n"
            f"│  `/quiz crypto` — Topic quiz\n"
            f"│  `/search <anything>` — AI search\n"
            f"└─────────────────────────────┘\n\n"
            f"┌─────────────────────────────┐\n"
            f"│  🏆  *LEADERBOARD*\n"
            f"│  `/lb` — Live rankings + Hall of Fame\n"
            f"└─────────────────────────────┘\n\n"
            f"┌─────────────────────────────┐\n"
            f"│  🎉  *FUN STUFF*\n"
            f"│  `/gay` — Daily gay detector 🌈\n"
            f"│  `/couple` — Today's ship 💞\n"
            f"└─────────────────────────────┘\n\n"
            f"┌─────────────────────────────┐\n"
            f"│  ⚡  *AUTO FEATURES*\n"
            f"│  📥 Paste YT / Instagram link\n"
            f"│     → auto downloads video\n"
            f"│  💬 Mention *Beluga* → AI chat\n"
            f"│  💬 Reply to me → AI chat\n"
            f"└─────────────────────────────┘\n"
            f"{owner_section}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔥 *Built by Team Oldy Crypto*\n"
            f"_Start chatting — I don't bite... much_ 😼"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[start] {e}", exc_info=True)

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
    logger.info("🐱  BELUGA BOT  v8.0.0")
    logger.info(f"   PORT={HTTP_PORT}  OWNER_ID={OWNER_ID}")
    logger.info(f"   SUPABASE_URL={'set ✅' if SUPABASE_URL else 'NOT SET ❌'}")
    logger.info(f"   SUPABASE_KEY={'set ✅' if SUPABASE_KEY else 'NOT SET ❌'}")
    logger.info("="*55)

    # 1. HTTP first
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    # 2. Load data from Supabase
    loop = asyncio.get_running_loop()
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            scores = await asyncio.wait_for(
                loop.run_in_executor(None, supabase_load_scores), timeout=20)
            if scores:
                db["scores"] = scores
                logger.info(f"✅ Scores loaded ({sum(len(v) for v in scores.values())} users)")
            winners = await asyncio.wait_for(
                loop.run_in_executor(None, supabase_load_weekly_winners), timeout=20)
            if winners:
                db["weekly_winners"] = winners
                logger.info(f"✅ Weekly winners loaded ({len(winners)} chats)")
            save_db()
        except Exception as e:
            logger.warning(f"[Supabase startup] {e} — using local cache")
    else:
        logger.warning("⚠️  SUPABASE_URL/KEY not set — add them to env vars for persistence!")

    # 3. Build PTB
    app = TGApp.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",              start_handler))
    app.add_handler(CommandHandler("search",             search_handler))
    app.add_handler(CommandHandler("quiz",               quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler("nw",                 nw_handler))
    app.add_handler(CommandHandler(["gay","couple"],     fun_dispatcher))
    app.add_handler(CommandHandler(["pump","dump"],      pump_dump_handler))
    app.add_handler(CommandHandler("tictac",             tictac_handler))
    app.add_handler(CallbackQueryHandler(ttt_callback,   pattern=r"^ttt:"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)
    logger.info("✅ All handlers registered")

    # 4. Start polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    bot_status["running"] = True
    logger.info("✅ Beluga v8.0.0 is LIVE 🐱")

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
    logger.info("🔄 Shutdown in progress…")
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
