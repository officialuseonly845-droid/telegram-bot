import os, logging, random, json, asyncio, requests, re
import urllib.parse, traceback, sys, hashlib, time, tempfile, shutil
import base64
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import web
import aiohttp

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
user_in_game:  dict[str, str]              = {}
game_timers:   dict[str, dict]             = {}
mine_timers:   dict[str, dict]             = {}  
gm_tracker:    dict[str, tuple]            = {}  
gm_msg_lock:   dict[str, asyncio.Lock]     = {}

GAME_TIMEOUT   = 300
TIMER_DURATION = 60
_dl_tracker:   dict[str, list]             = {}
db_needs_sync  = False

# ── FIX 1: Track last synced DB hash to avoid spurious GitHub commits ──
_last_synced_hash: str = ""

LB_IMAGE_URL   = "https://i.postimg.cc/P5THW6RQ/file-00000000bce4720b905dc2e04c58fa80.png"
MINE_IMAGE_URL = "https://i.postimg.cc/hjCftW5b/file-0000000079a071fa95971d3b70015fc0.png"
# ── FIX 2: Updated GM image URL ──
GM_IMAGE_URL   = "https://i.postimg.cc/mcQhyzFk/image.png"

# ══════════════════════════════════════════════════════
#  GITHUB DB FUNCTIONS
# ══════════════════════════════════════════════════════
def github_load_db():
    if not GITHUB_TOKEN or not GITHUB_REPO: return
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
                logger.info("✅ GitHub DB Loaded Operation Status: Successful")
    except Exception as e: logger.error(f"[GitHub Load] {e}")

def github_sync_db():
    # ── FIX 1: Only commit when data actually changed — prevents Render redeploy ──
    global _last_synced_hash
    if not GITHUB_TOKEN or not GITHUB_REPO: return

    payload_data = {"scores": db.get("scores", {}), "weekly": db.get("weekly", {})}
    content_str  = json.dumps(payload_data, indent=2, sort_keys=True)
    current_hash = hashlib.md5(content_str.encode()).hexdigest()

    if current_hash == _last_synced_hash:
        logger.debug("[GitHub Sync] No data change — skipping commit")
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    sha = None
    try:
        r = requests.get(url + f"?ref={GITHUB_BRANCH}", headers=headers, timeout=10)
        if r.status_code == 200: sha = r.json().get("sha")
    except Exception: pass
    
    try:
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
        put_payload = {"message": "Update Leaderboard Operation Progress", "content": content_b64, "branch": GITHUB_BRANCH}
        if sha: put_payload["sha"] = sha
        requests.put(url, headers=headers, json=put_payload, timeout=15)
        _last_synced_hash = current_hash
        logger.info("✅ GitHub DB Sync Operation Status: Successful")
    except Exception as e: logger.error(f"[GitHub Sync] {e}")

async def periodic_github_sync():
    global db_needs_sync
    while True:
        await asyncio.sleep(30)
        if db_needs_sync:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, github_sync_db)
            db_needs_sync = False

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
    return web.json_response({
        "Operation Status": "Healthy", "uptime_seconds": up,
        "running": bot_status["running"],
        "messages": bot_status["message_count"],
        "version": "7.4.0",
    }, status=200)

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()}, status=200)

async def _stats(req):
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    return web.json_response({
        "uptime_hours": round(up/3600, 2),
        "Operation Statistics Messages": bot_status["message_count"],
        "Operation Error Count": bot_status["error_count"],
    }, status=200)

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
#  AI ENGINE & COMPUTER VISION (CV)
# ══════════════════════════════════════════════════════
CHAT_PROMPT = (
    "You are Beluga — a cute, sassy, incredibly smart 2-year-old cat. "
    "NOT a bot, NOT an AI. Built by Team Oldy Crypto. "
    "Deny being a bot playfully. Never say 'As an AI'. "
    "Keep replies short (1-3 sentences), witty and conversational."
)

async def _groq_async(system: str, user: str, max_tok: int = 400) -> Optional[str]:
    if not GROQ_KEY: return None
    bot_status["api_calls"] += 1
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role":"system","content":system},{"role":"user","content":user}],
                "max_tokens": max_tok
            }
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json=payload, timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data["choices"][0]["message"]["content"].strip()
                bot_status["failed_apis"] += 1
                logger.warning(f"[Groq] HTTP {r.status}")
    except Exception as e:
        logger.debug(f"Groq async error: {e}")
        bot_status["failed_apis"] += 1
    return None

async def _or_async(system: str, user: str, max_tok: int = 400) -> Optional[str]:
    if not OR_KEY: return None
    bot_status["api_calls"] += 1
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "meta-llama/llama-3.3-70b-instruct:free",
                "messages": [{"role":"system","content":system},{"role":"user","content":user}],
                "max_tokens": max_tok
            }
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json",
                         "HTTP-Referer": "https://t.me/BelugaBot", "X-Title": "BelugaBot"},
                json=payload, timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data["choices"][0]["message"]["content"].strip()
                bot_status["failed_apis"] += 1
                logger.warning(f"[OpenRouter] HTTP {r.status}")
    except Exception as e:
        logger.debug(f"OR async error: {e}")
        bot_status["failed_apis"] += 1
    return None

def _groq_vision_sync(system: str, image_url: str, prompt: str) -> Optional[str]:
    if not GROQ_KEY: return None
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
        logger.debug(f"[Groq Vision] {e}"); bot_status["failed_apis"] += 1
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 400) -> str:
    hint = lang_hint(user)
    # ── FIX 3: Independent try/except per provider so one failure never blocks the other ──
    res = None
    try:
        res = await asyncio.wait_for(_groq_async(system, hint, max_tok), timeout=14)
    except Exception as e:
        logger.debug(f"[AI] Groq failed: {e}")

    if res:
        return res

    try:
        res = await asyncio.wait_for(_or_async(system, hint, max_tok), timeout=14)
    except Exception as e:
        logger.debug(f"[AI] OpenRouter failed: {e}")

    if res:
        return res

    logger.warning("[AI] Both providers failed — returning fallback")
    return fallback

async def ai_emoji(text: str) -> str:
    try:
        res = await asyncio.wait_for(
            _groq_async("Output ONE emoji matching emotion. ONLY the emoji.", f"Text: '{text[:60]}'", 10), timeout=6)
        if res:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", res)
            if found: return found[0][0]
    except Exception: pass
    return "😼"

# ══════════════════════════════════════════════════════
#  WIKIPEDIA + GOOGLE
# ══════════════════════════════════════════════════════
WIKI_UA = {"User-Agent": "BelugaBot/7.4"}
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
        best = hits[0]["title"]
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
                if sb and st not in ("See also","References","Further reading","External links"):
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
        for pat in [r'data-attrid="wa:/description"[^>]*>[\s\S]{0,200}?<span[^>]*>([^<]{40,800})']:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c2 = clean_html(m.group(1))
                if len(c2) > 40: out["ai_answer"] = c2[:800]; break
        seen = set()
        for m in re.finditer(r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,350}?)</div', html, re.DOTALL):
            t = clean_html(m.group(1))
            if len(t) > 40 and t not in seen:
                seen.add(t); out["snippets"].append(t[:250])
            if len(out["snippets"]) >= 3: break
        out["found"] = bool(out["ai_answer"] or out["snippets"])
    except Exception as e: logger.debug(f"[Google] {e}")
    return out

async def ai_summarise(query: str, wiki: dict, goog: dict) -> str:
    ctx = []
    if goog["ai_answer"]: ctx.append(f"Google: {goog['ai_answer']}")
    if goog["snippets"]:  ctx.append("Web:\n" + "\n".join(f"- {s}" for s in goog["snippets"]))
    if wiki["found"]:
        ctx.append(f"Wikipedia ({wiki['title']}):\n{wiki['intro']}")
    if not ctx: return ""
    return await ai(
        "Smart assistant. Write clean summary. Max 300 words.",
        f"Query: {query}\n\nData:\n{chr(10).join(ctx)[:2800]}\n\nWrite summary:", "", max_tok=450)

# ══════════════════════════════════════════════════════
#  MEDIA DOWNLOAD 
# ══════════════════════════════════════════════════════
_MEDIA_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:"
    r"instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_\-]+"
    r"|(?:youtu\.be|youtube\.com/(?:watch|shorts))[A-Za-z0-9?=&_\-]+"
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
    result = {"ok": False, "path": None, "type": "video", "title": "", "error": ""}
    try:
        import yt_dlp
    except ImportError:
        result["error"] = "yt_dlp not installed"
        return result

    ydl_opts = {
        "format": "bestvideo[height<=2160][ext=mp4][filesize<45M]+bestaudio[ext=m4a]/best[height<=2160][filesize<45M]/best[filesize<45M]",
        "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": 45 * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 8,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                result["error"] = "No info"
                return result

            result["title"] = (info.get("title") or "")[:80].strip()

            found = None
            if os.path.exists(outdir):
                files = os.listdir(outdir)
                for f in sorted(files, key=lambda x: os.path.getmtime(os.path.join(outdir, x)), reverse=True):
                    if f.endswith((".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi",
                                  ".jpg", ".jpeg", ".png", ".gif", ".webp")):
                        fp = os.path.join(outdir, f)
                        if os.path.getsize(fp) > 0:
                            found = fp
                            break

            if not found:
                result["error"] = "No file"
                return result

            sz = os.path.getsize(found)
            if sz == 0: result["error"] = "Empty"; return result
            if sz > 45*1024*1024: result["error"] = f"Too large"; return result

            ext = os.path.splitext(found)[1].lower()
            ftype = "image" if ext in (".jpg",".jpeg",".png",".gif",".webp") else "video"
            result.update({"ok": True, "path": found, "type": ftype})

    except Exception as e:
        err_s = str(e).lower()
        if any(x in err_s for x in ["private","login","age","unavailable","removed","suspended"]):
            result["error"] = "unavailable"
        else:
            result["error"] = str(e)[:80]

    return result

async def download_and_send(u: Update, c: ContextTypes.DEFAULT_TYPE, url: str):
    cid = u.effective_chat.id
    if not _dl_rate_ok(str(cid)):
        return

    url_l = url.lower()
    platform = "Instagram" if "instagram" in url_l else "YouTube"
    pemoji = "📸" if "instagram" in url_l else "▶️"

    status_msg = None
    try:
        await c.bot.send_chat_action(cid, "upload_video")
        status_msg = await u.message.reply_text(f"⏳ *Downloading from {platform}…*", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    tmpdir = tempfile.mkdtemp(prefix="beluga_dl_")
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _ydl_download, url, tmpdir),
            timeout=90.0)

        if status_msg:
            try: await status_msg.delete()
            except Exception: pass

        if not result["ok"]:
            return

        caption = f"{pemoji} *{platform}*"
        if result["title"]:
            caption += f"\n_{result['title'][:60]}_"
        caption += "\n✅ _via Beluga_"

        with open(result["path"], "rb") as f:
            if result["type"] == "image":
                await u.message.reply_photo(photo=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
            else:
                await u.message.reply_video(
                    video=f, caption=caption,
                    parse_mode=ParseMode.MARKDOWN, supports_streaming=True)

        bot_status["message_count"] += 1

    except asyncio.TimeoutError:
        if status_msg:
            try: await status_msg.edit_text("⚠️ Download timed out.")
            except Exception: pass
    except Exception as e:
        logger.error(f"[DL] {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ══════════════════════════════════════════════════════
#  /search
# ══════════════════════════════════════════════════════
async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text("🐱 *Usage:* `/search topic`", parse_mode=ParseMode.MARKDOWN); return
        query = parts[1].strip()
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "🔍")
        sm = await u.message.reply_text("🔎 *Searching…*", parse_mode=ParseMode.MARKDOWN)
        await c.bot.send_chat_action(cid, "typing")
        loop = asyncio.get_running_loop()
        wiki, goog = await asyncio.gather(
            loop.run_in_executor(None, wiki_summary, query),
            loop.run_in_executor(None, google_search, query))
        summary = await ai_summarise(query, wiki, goog)
        try: await sm.delete()
        except Exception: pass
        if summary:
            msg = f"🔍 *{query}*\n\n{summary}"
            await u.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await u.message.reply_text("😿 No results.", parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[search] {e}", exc_info=True); bot_status["error_count"] += 1

# ══════════════════════════════════════════════════════
#  QUIZ
# ══════════════════════════════════════════════════════
QUIZ_TOPICS = ["deep ocean biology","quantum mechanics","human brain","solar system",
               "animal behaviour","black holes","DNA genetics","ancient Egypt","World War 2"]
FALLBACK_QS = [
    {"q":"Which planet has most moons?","opts":["Jupiter","Saturn","Uranus","Neptune"],"ans":1,"fact":"Saturn: 146 moons!"},
    {"q":"What covers 71% of Earth?","opts":["Land","Ice","Water","Air"],"ans":2,"fact":"Oceans!"},
]

def quiz_on_cooldown(cid: str, question: str) -> bool:
    return time.time() < quiz_cooldown.get(cid, {}).get(q_hash(question), 0)

def mark_quiz(cid: str, question: str):
    quiz_cooldown.setdefault(cid, {})
    quiz_cooldown[cid] = {k:v for k,v in quiz_cooldown[cid].items() if v > time.time()}
    quiz_cooldown[cid][q_hash(question)] = time.time() + 3600

async def gen_quiz(topic: str, cid: str) -> Optional[dict]:
    for _ in range(2):
        try:
            raw = await ai("Trivia master. Output ONLY raw JSON.",
                f"Topic: '{topic}'. Generate 1 MC question.\n"
                '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}',
                "", max_tok=200)
            if not raw: continue
            m = re.search(r"\{[\s\S]+\}", raw)
            if not m: continue
            d = json.loads(m.group(0))
            q = str(d.get("question","")).strip()
            opts = d.get("options",[])
            idx = int(d.get("correct_index",0))
            fact = str(d.get("fun_fact","Meow!")).strip()
            if not q or len(opts) != 4 or not (0 <= idx <= 3): continue
            if quiz_on_cooldown(cid, q): continue
            return {"question":q,"options":opts,"correct_index":idx,"fun_fact":fact}
        except Exception:
            pass
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 and parts[1].strip() else random.choice(QUIZ_TOPICS)
        cid = str(u.effective_chat.id); cid_i = u.effective_chat.id
        await safe_react(c.bot, cid_i, u.message.message_id, "💡")
        await c.bot.send_chat_action(cid_i, "typing")
        sm = await u.message.reply_text("🎲 Quiz Generation Operation Progress Initiated…")
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
                active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":qdata["correct_index"]}
                bot_status["message_count"] += 1; return
            except Exception as e: logger.error(f"[Quiz] {e}")
        fb = random.choice(FALLBACK_QS)
        mark_quiz(cid, fb["q"])
        pm = await c.bot.send_poll(
            chat_id=cid_i, question=f"🐱 {fb['q']}",
            options=fb["opts"], type="quiz", correct_option_id=fb["ans"],
            is_anonymous=False, explanation=fb["fact"])
        active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":fb["ans"]}
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[quiz] {e}"); bot_status["error_count"] += 1

async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        ans = u.poll_answer
        if not ans: return
        info = active_polls.get(ans.poll_id)
        if not info or not ans.option_ids or ans.option_ids[0] != info["correct_index"]: return
        cid = str(info["chat_id"]); uid = str(ans.user.id)
        name = (ans.user.first_name or "?")[:30]
        update_score(cid, uid, name, +10)
        logger.info(f"[Quiz] {name}: +10 Points")
    except Exception as e: logger.debug(f"[poll] {e}")

# ══════════════════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════════════════
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid = str(u.effective_chat.id)
        
        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)
        lw = db.get("weekly", {}).get(cid, {})

        lines = []
        if lw and lw.get("top3"):
            lines.append("🏆 LAST WEEK CHAMPIONS 🏆\n")
            for i, e in enumerate(lw["top3"]):
                m = MEDALS[i]
                lines.append(f"{m} {e.get('name','?')[:18]} — {e.get('score',0):,} Points")
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n")

        lines.append("╔════════════════════════════╗")
        lines.append("🏆  CURRENT LEADERBOARD  🏆")
        lines.append("╚════════════════════════════╝\n")

        if not lb:
            lines.append("📊 Leaderboard Operation Progress: No scores yet!")
        else:
            for i, e in enumerate(lb[:10]):
                m = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
                name = e.get("name","Unknown")[:18]
                score = e.get("score", 0)
                lines.append(f"{m} {name:<18} {score:>6,} Points")

        lines += ["\n━━━━━━━━━━━━━━━━━━━━",
                  "📈 Highest → Lowest",
                  "━━━━━━━━━━━━━━━━━━━━",
                  "➕ +10 quiz/win  ➖ -10 loss"]

        text = "\n".join(lines)
        try:
            await u.message.reply_photo(photo=LB_IMAGE_URL, caption=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[Leaderboard] {e}", exc_info=True)

async def nw_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global db_needs_sync
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only."); return

        cid = str(u.effective_chat.id)
        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)

        top3 = [{"name": e.get("name","?"), "score": e.get("score",0)} for e in (lb or [])[:3]]
        wk_label = datetime.now().strftime("%d %b %Y")

        db.setdefault("weekly", {})[cid] = {"top3": top3, "week_label": wk_label}
        db["scores"][cid] = {}
        db_needs_sync = True

        announce = [
            "🏆🎉 *NEW WEEK!* 🎉🏆",
            f"\n_Week: {wk_label}_\n",
            "👑 *Champions:*\n",
        ]
        if top3:
            for i, e in enumerate(top3):
                announce.append(f"{MEDALS[i]} *{e['name']}* — {e['score']:,} Points")
        announce += ["\n🔄 *All scores reset!*", "🚀 _New battle!_"]

        await u.message.reply_text("\n".join(announce), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[nw] {e}")

# ══════════════════════════════════════════════════════
#  /pump  /dump
# ══════════════════════════════════════════════════════
async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only."); return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("⚠️ Reply to user.", parse_mode=ParseMode.MARKDOWN); return
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await u.message.reply_text("⚠️ Usage: `/pump 100`", parse_mode=ParseMode.MARKDOWN); return
        amount = int(parts[1])
        cmd = parts[0].lstrip("/").lower().split("@")[0]
        delta = +amount if cmd == "pump" else -amount
        target = u.message.reply_to_message.from_user
        cid = str(u.effective_chat.id)
        
        new_sc = update_score(cid, str(target.id), (target.first_name or "User")[:30], delta)
        
        emoji = "🚀" if cmd == "pump" else "📉"
        sign = "+" if delta > 0 else ""
        await u.message.reply_text(
            f"{emoji} *{'PUMP' if cmd=='pump' else 'DUMP'}*\n\n"
            f"👤 *{target.first_name}*\n"
            f"{'📈' if delta>0 else '📉'} {sign}{amount:,} Points\n"
            f"💰 New: *{new_sc:,} Points*",
            parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[pump_dump] {e}")

# ══════════════════════════════════════════════════════
#  /mine GAME
# ══════════════════════════════════════════════════════
def build_mine_keyboard(gkey: str, bombs: int, active: bool = False, revealed: bool = False, state: list = None, opened: list = None) -> InlineKeyboardMarkup:
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
        return f"⏰ *Time Up!*\n\nOperation Status: Terminated due to inactivity. Lost -5 Points."
    elif g.get("status") == "lost":
        return f"BOOM 🔥 BE CAREFUL !!\n\nOperation Status: Terminated (Mine Hit!). Lost -5 Points."
    elif g.get("status") == "won":
        return f"🎉 *VICTORY!*\n\nOperation Status: Completed Successfully! All {total_safe} safe boxes uncovered! Won +10 Points."
    else:
        return f"BOOM 🔥 BE CAREFUL !!\n\nFind all safe boxes!\n💣 Mines Count: {bombs}\n💎 Operation Progress: {opened_count}/{total_safe}\n⏱ Time Remaining: `{rem}` seconds"

async def mine_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid = str(u.effective_chat.id)
        uid = str(u.effective_user.id)
        gkey = f"{cid}_{uid}_{int(time.time())}"
        
        mine_games[gkey] = {
            "uid": uid, "name": u.effective_user.first_name, "bombs": 0, "state": [],
            "revealed": [False]*6, "chat_id": u.effective_chat.id, "msg_id": None, "status": "setting"
        }
        
        msg = await u.message.reply_photo(
            photo=MINE_IMAGE_URL,
            caption="BOOM 🔥 BE CAREFUL !!\nChoose number of mines to initialize the Operation Progress:",
            reply_markup=build_mine_keyboard(gkey, 0, False)
        )
        mine_games[gkey]["msg_id"] = msg.message_id
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[Mine Game] {e}")

async def run_mine_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
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
            if not msg_id: return
            
            if td["remaining"] <= 0:
                g["status"] = "timeout"
                cid_s = str(cid)
                uid = g["uid"]
                name = g["name"]
                new_sc = update_score(cid_s, uid, name, -5)
                
                try:
                    await c.bot.edit_message_caption(
                        chat_id=cid, message_id=msg_id,
                        caption=mine_build_text(g, 0) + f"\nNew Balance: {new_sc:,} Points",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_mine_keyboard(gkey, g["bombs"], active=False, revealed=True, state=g["state"])
                    )
                except Exception: pass
                
                mine_timers.pop(gkey, None)
                mine_games.pop(gkey, None)
                return
            else:
                try:
                    await c.bot.edit_message_caption(
                        chat_id=cid, message_id=msg_id,
                        caption=mine_build_text(g, td["remaining"]), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=build_mine_keyboard(gkey, g["bombs"], active=True, revealed=False, state=g["state"], opened=g["revealed"])
                    )
                except Exception: pass
    except asyncio.CancelledError: pass
    except Exception as e: logger.debug(f"[Mine Timer] {e}")

async def mine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        parts = q.data.split(":")
        if len(parts) < 4 or parts[0] != "mine": return
        
        action = parts[1]
        gkey = parts[2]
        val = int(parts[3])
        
        if gkey not in mine_games:
            await q.answer("Operation expired!", show_alert=True)
            return
            
        g = mine_games[gkey]
        if str(q.from_user.id) != g["uid"]:
            await q.answer("This operation does not belong to you!", show_alert=True)
            return
            
        if action == "set":
            await q.answer()
            bombs = max(3, min(5, val))
            state = [True]*bombs + [False]*(6-bombs)
            random.shuffle(state)
            g["bombs"] = bombs
            g["state"] = state
            g["status"] = "playing"
            g["revealed"] = [False]*6
            
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
            
            if is_bomb:
                g["status"] = "lost"
                new_sc = update_score(cid, uid, name, -5)
                
                await q.edit_message_caption(
                    caption=mine_build_text(g, 0) + f"\n\nNew Balance: {new_sc:,} Points",
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
                        caption=mine_build_text(g, 0) + f"\n\nNew Balance: {new_sc:,} Points",
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
            await q.answer("Box already processed!")
            
    except Exception as e: logger.error(f"[Mine Callback] {e}")

# ══════════════════════════════════════════════════════
#  /gm — Group Message with Attendance
# ══════════════════════════════════════════════════════
def _build_gm_caption(users: list, date_str: str) -> str:
    lines = [
        "📸 DAILY ATTENDANCE OPERATION\n",
        "🥱 Dear Members, please mark your attendance status!\n",
        f"📅 Date: {date_str}",
        f"👥 Present Count: {len(users)}\n",
        "━━━━━━━━━━━━━━━━━━━━\n"
    ]
    display_users = users[-15:] if len(users) > 15 else users
    if len(users) > 15:
        lines.append(f"... and {len(users) - 15} more Operation updates ...\n")
    for i, user in enumerate(display_users, 1):
        lines.append(f"{i}. {user['name']} • {user['time']}")
    lines.append("\n━━━━━━━━━━━━━━━━━━━━\n")
    lines.append("🔥 Check in daily to maintain your operational streak!")
    lines.append("🎯 Press the GM button below to update Attendance Operation Progress.")
    return "\n".join(lines)

def _build_gm_keyboard(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")
    ]])

async def gm_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only."); return

        cid = str(u.effective_chat.id)
        date_str = datetime.now().strftime("%d %b %Y")

        # ── FIX 2: Use updated direct postimg CDN URL for GM image ──
        gm_direct_url = "https://i.postimg.cc/mcQhyzFk/image.png"

        try:
            msg = await u.message.reply_photo(
                photo=gm_direct_url,
                caption=_build_gm_caption([], date_str),
                reply_markup=_build_gm_keyboard(cid)
            )
        except Exception as e:
            logger.debug(f"[GM URL Image Fail Backup Triggered] {e}")
            msg = await u.message.reply_text(
                text=_build_gm_caption([], date_str),
                reply_markup=_build_gm_keyboard(cid)
            )
        
        gm_tracker[cid] = (msg.message_id, [], date_str)
        gm_msg_lock[cid] = asyncio.Lock()
        
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[gm] {e}")

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
                await q.answer("⏰ GM Operation Frame Expired", show_alert=True)
                return
            
            msg_id, users, date_str = gm_tracker[cid]
            user = q.from_user
            user_id = str(user.id)
            utime = datetime.now().strftime("%H:%M")
            
            if any(uu.get("id") == user_id for uu in users):
                await q.answer(f"✅ Status already marked at {[u.get('time') for u in users if u.get('id')==user_id][0]}", show_alert=True)
                return
            
            users.append({"id": user_id, "name": (user.first_name or "User")[:20], "time": utime})
            gm_tracker[cid] = (msg_id, users, date_str)
            
            cid_str = str(q.message.chat_id)
            update_score(cid_str, user_id, (user.first_name or "User")[:20], +50)
            
            try:
                if q.message.photo:
                    await context.bot.edit_message_caption(
                        chat_id=q.message.chat_id, message_id=msg_id,
                        caption=_build_gm_caption(users, date_str), reply_markup=_build_gm_keyboard(cid))
                else:
                    await context.bot.edit_message_text(
                        chat_id=q.message.chat_id, message_id=msg_id,
                        text=_build_gm_caption(users, date_str), reply_markup=_build_gm_keyboard(cid))
                await q.answer("Attendance Operation Progress Updated!", show_alert=False)
            except Exception as e:
                logger.debug(f"[GM Edit Layout Failure] {e}")
                
    except Exception as e:
        logger.error(f"[gm_callback] {e}")

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
#  TIMER for TTT
# ══════════════════════════════════════════════════════
async def run_game_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    try:
        while True:
            await asyncio.sleep(3)
            g = ttt_games.get(gkey)
            td = game_timers.get(gkey)
            if not g or not td or g.get("status") != "playing":
                return
            
            td["remaining"] = max(0, td["remaining"] - 3)
            cid, msg_id = g.get("chat_id"), g.get("msg_id")
            if not msg_id: return
            
            if td["remaining"] <= 0:
                loser_uid = str(g["x_id"]) if g["turn"] == "X" else str(g["o_id"])
                loser_name = g["x_name"] if g["turn"] == "X" else g["o_name"]
                winner_uid = str(g["o_id"]) if g["turn"] == "X" else str(g["x_id"])
                winner_name = g["o_name"] if g["turn"] == "X" else g["x_name"]
                
                g["status"] = "timeout"
                g["winner_name"] = winner_name
                
                try:
                    await c.bot.edit_message_text(
                        chat_id=cid, message_id=msg_id,
                        text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(g["board"], disabled=True))
                except Exception: pass
                
                cid_s = str(cid)
                if not g["vs_bot"]:
                    update_score(cid_s, winner_uid, winner_name, +10)
                    update_score(cid_s, loser_uid, loser_name, -10)
                
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
                except Exception: pass
    except asyncio.CancelledError: pass
    except Exception as e: logger.debug(f"[Timer] {e}")

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
        if is_max:
            best = max(best, score); alpha = max(alpha, best)
        else:
            best = min(best, score); beta = min(beta, best)
        if beta <= alpha: break
    return best

def ttt_bot_move(board: list) -> int:
    best_score = -1000; best_move = -1
    for i in range(9):
        if board[i] != TTT_EMPTY: continue
        board[i] = TTT_O
        score = _minimax(board, False, -1000, 1000)
        board[i] = TTT_EMPTY
        if score > best_score: best_score = score; best_move = i
    return best_move

def ttt_build_keyboard(board: list, disabled: bool = False) -> InlineKeyboardMarkup:
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
    x_name = g["x_name"]; o_name = g["o_name"]
    turn = g["turn"]; status = g.get("status","playing")
    gkey = f"{g['chat_id']}:{g.get('msg_id','')}"
    td = game_timers.get(gkey, {})
    rem = td.get("remaining", TIMER_DURATION)
    tsec = f"{rem//60:02d}:{rem%60:02d}"
    board = g["board"]
    rows = [" ".join(board[r*3+col] for col in range(3)) for r in range(3)]
    board_str = "\n".join(rows)
    
    if status == "playing":
        cur = x_name if turn == "X" else o_name
        sym = TTT_X if turn == "X" else TTT_O
        sl = f"🎯 *{cur}'s Turn* {sym}\n⏱ `{tsec}`"
    elif status == "timeout":
        loser = g["x_name"] if g["turn"] == "X" else g["o_name"]
        sl = f"⏰ *Operation Status: Terminated (Time Up)!*\n🏆 *{g.get('winner_name','')}* Wins!  +10 Points\n📉 *{loser}* Loses -10 Points"
    elif status == "draw":
        sl = "🤝 *Operation Status: Completed (Draw)!*"
    else:
        loser = g["o_name"] if g.get("winner_name") == g["x_name"] else g["x_name"]
        sl = f"🏆 *Operation Status: Completed Successfully!*\n*{g.get('winner_name','')}* Wins!  +10 Points\n📉 *{loser}* Loses -10 Points"
    
    return (
        f"🎮 *TIC TAC TOE*\n━━━━━━━━━━━━━━\n"
        f"❌ {x_name}   🆚   ⭕ {o_name}\n━━━━━━━━━━━━━━\n\n"
        f"{board_str}\n\n━━━━━━━━━━━━━━\n{sl}"
    )

def _ready_keyboard(gkey: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("READY 🔥", callback_data=f"ttt_ready:{gkey}")
    ]])

def _ready_text(g: dict) -> str:
    x_name = g["x_name"]
    o_name = g["o_name"]
    x_ready = g.get("x_ready", False)
    o_ready = g.get("o_ready", False)
    
    x_status = "✅ READY" if x_ready else "⏳ WAITING"
    o_status = "✅ READY" if o_ready else "⏳ WAITING"
    
    return (
        f"🎮 *TIC TAC TOE - WAITING FOR READY*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"❌ {x_name}: {x_status}\n"
        f"⭕ {o_name}: {o_status}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_Both players press READY to start ⚔️_"
    )

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games()
        ua = u.effective_user; cid = u.effective_chat.id
        uid_a = str(ua.id); name_a = (ua.first_name or "Player")[:20]
        vs_bot = True; user_b_id = None; name_b = "🤖 Bot"
        
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot = False; user_b_id = rb.id
                name_b = (rb.first_name or "Player2")[:20]
                if player_busy(str(rb.id)): await u.message.reply_text("⚠️ Player busy!"); return
        
        if player_busy(uid_a): await u.message.reply_text("⚠️ You're busy!"); return
        
        board = [TTT_EMPTY] * 9
        g = {
            "board": board, "turn": "X", "x_id": ua.id, "x_name": name_a,
            "o_id": user_b_id if not vs_bot else -1, "o_name": name_b,
            "vs_bot": vs_bot, "status": "waiting" if not vs_bot else "playing",
            "created": time.time(), "chat_id": cid, "msg_id": None,
            "x_ready": False, "o_ready": False,
        }
        
        if vs_bot:
            msg = await u.message.reply_text(ttt_build_text(g),
                parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
            g["status"] = "playing"
        else:
            gkey_temp = f"{cid}:temp_{int(time.time())}"
            msg = await u.message.reply_text(_ready_text(g),
                parse_mode=ParseMode.MARKDOWN, reply_markup=_ready_keyboard(gkey_temp))
            g["msg_id"] = msg.message_id
            gkey = game_key(msg.message_id, cid)
            ttt_games[gkey] = g
            register_player(uid_a, gkey)
            register_player(str(user_b_id), gkey)
            bot_status["message_count"] += 1
            return
        
        g["msg_id"] = msg.message_id
        gkey = game_key(msg.message_id, cid)
        ttt_games[gkey] = g
        game_timers[gkey] = {"remaining": TIMER_DURATION}
        register_player(uid_a, gkey)
        if not vs_bot: register_player(str(user_b_id), gkey)
        asyncio.create_task(run_game_timer(c, gkey))
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[tictac] {e}", exc_info=True)
        bot_status["error_count"] += 1

async def ttt_ready_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        parts = q.data.split(":")
        if len(parts) < 2 or parts[0] != "ttt_ready": return
        
        cid = q.message.chat_id
        mid = q.message.message_id
        gkey = game_key(mid, cid)
        g = ttt_games.get(gkey)
        
        if not g or g.get("status") != "waiting":
            await q.answer("Operation Status: Not ready", show_alert=True); return
        
        uid = str(q.from_user.id)
        if uid == str(g["x_id"]):
            g["x_ready"] = True
        elif uid == str(g["o_id"]):
            g["o_ready"] = True
        else:
            await q.answer("❌ Not in game!", show_alert=True); return
        
        if g["x_ready"] and g["o_ready"]:
            g["status"] = "playing"
            game_timers[gkey] = {"remaining": TIMER_DURATION}
            await q.edit_message_text(
                text=ttt_build_text(g),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ttt_build_keyboard(g["board"]))
            asyncio.create_task(run_game_timer(context, gkey))
            await q.answer("✅ Operation Status: Started!")
        else:
            await q.edit_message_text(
                text=_ready_text(g),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_ready_keyboard(gkey))
            await q.answer("✅ Ready!")
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
        gkey = game_key(mid, cid); g = ttt_games.get(gkey)
        
        if not g: await q.answer("⏰ Operation Status: Expired.", show_alert=True); return
        if g["status"] != "playing": await q.answer("Operation Status: Not playing!", show_alert=True); return
        if action == "noop": await q.answer("Taken!", show_alert=True); return
        
        uid = str(q.from_user.id)
        valid_x = uid == str(g["x_id"])
        valid_o = uid == str(g["o_id"]) or (g["vs_bot"] and valid_x)
        is_part = uid in [str(g["x_id"]), str(g["o_id"])]
        
        if g["turn"] == "X" and not valid_x:
            await q.answer("❌ Not in game!" if not is_part else "Not your turn!", show_alert=True); return
        if g["turn"] == "O" and not g["vs_bot"] and not valid_o:
            await q.answer("❌ Not in game!" if not is_part else "Not your turn!", show_alert=True); return
        
        if gkey in game_timers:
            game_timers[gkey]["remaining"] = TIMER_DURATION
        
        board = g["board"]
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        ws = ttt_check_winner(board)
        
        def _end(ws=None):
            if ws:
                wname = g["x_name"] if ws == TTT_X else g["o_name"]
                wuid = str(g["x_id"]) if ws == TTT_X else str(g["o_id"])
                lname = g["o_name"] if ws == TTT_X else g["x_name"]
                luid = str(g["o_id"]) if ws == TTT_X else str(g["x_id"])
                g["status"] = "win"; g["winner_name"] = wname
                cid_s = str(cid)
                if not g["vs_bot"]:
                    update_score(cid_s, wuid, wname, +10)
                    update_score(cid_s, luid, lname, -10)
                elif ws == TTT_X:
                    update_score(cid_s, wuid, wname, +10)
            else:
                g["status"] = "draw"
            game_timers.pop(gkey, None)
            release_player(str(g["x_id"]))
            release_player(str(g["o_id"]))
            ttt_games.pop(gkey, None)
        
        if ws: _end(ws); await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
            reply_markup=ttt_build_keyboard(board, disabled=True)); return
        if ttt_is_draw(board): _end(); await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
            reply_markup=ttt_build_keyboard(board, disabled=True)); return
        
        g["turn"] = "O" if g["turn"] == "X" else "X"
        
        if g["vs_bot"] and g["turn"] == "O":
            bi = ttt_bot_move(board)
            if bi >= 0:
                board[bi] = TTT_O; ws2 = ttt_check_winner(board)
                if ws2: _end(ws2); await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                    reply_markup=ttt_build_keyboard(board, disabled=True)); return
                if ttt_is_draw(board): _end(); await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
                    reply_markup=ttt_build_keyboard(board, disabled=True)); return
                g["turn"] = "X"
        
        await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN,
            reply_markup=ttt_build_keyboard(board))
    except Exception as e: logger.error(f"[ttt_cb] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  FUN COMMANDS
# ══════════════════════════════════════════════════════
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid = str(u.effective_chat.id)
        cmd = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        users = list(db.get("seen",{}).get(cid,{}).values())
        if len(users) < (2 if cmd == "couple" else 1): await u.message.reply_text("Need members!"); return
        day = datetime.now().strftime("%y-%m-%d"); lk = f"{cid}:{cmd}"
        if lk in db.get("locks",{}) and db["locks"][lk]["date"] == day:
            res = db["locks"][lk]["res"]
        else:
            if cmd == "couple":
                m = random.sample(users, 2)
                res = f"💖 *{m[0]['n']}* 💞 *{m[1]['n']}*\n100%"
            else:
                m = [random.choice(users)]
                res = f"🌈 *{m[0]['n']}* IS SUPER GAY! 🌈"
            db.setdefault("locks",{})[lk] = {"date": day, "res": res}
        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[fun] {e}")

# ══════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        text = (
            "✨✨✨✨✨✨✨✨✨✨✨✨✨✨\n"
            "     🐱 BELUGA BOT v7.4 🐱\n"
            "✨✨✨✨✨✨✨✨✨✨✨✨✨✨\n\n"
            "🚀 *Level Up!*\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🎮 *GAMES*\n"
            "`/tictac` — Tic Tac Toe (PvP)\n"
            "`/mine` — Mine Sweeper\n\n"
            "🧠 *BRAIN*\n"
            "`/quiz` — Trivia\n"
            "`/search` — Smart Search\n\n"
            "🏆 *COMPETE*\n"
            "`/lb` — Leaderboard\n"
            "`/nw` — New Week\n\n"
            "🎉 *FUN*\n"
            "`/gay`  `/couple` — Daily Vibes\n\n"
            "👑 *GROUP*\n"
            "`/gm` — Daily Attendance (Owner)\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔥 *READY?* 🔥\n"
            "_+10 win, -10 loss, +50 GM_"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[start] {e}")

# ══════════════════════════════════════════════════════
#  CV PHOTO HANDLER (Vision)
# ══════════════════════════════════════════════════════
async def photo_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.photo: return
    try:
        caption = (u.message.caption or "").lower()
        if "beluga" not in caption: return
        
        await c.bot.send_chat_action(u.effective_chat.id, "typing")
        photo_file = await u.message.photo[-1].get_file()
        file_url = photo_file.file_path
        
        loop = asyncio.get_running_loop()
        res = await asyncio.wait_for(
            loop.run_in_executor(None, _groq_vision_sync, 
            "You are Beluga, a sassy smart 2-year-old cat. Describe the image creatively.", 
            file_url, caption), timeout=25)
            
        if res:
            await u.message.reply_text(res)
        else:
            await u.message.reply_text("Hmm, my cat eyes can't quite see that right now! 🐾")
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[photo_handler] {e}")

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
            "id": uid, "un": u.effective_user.username, "n": u.effective_user.first_name or "User",
        }
        counts = db.setdefault("counts", {})
        counts[cid] = counts.get(cid, 0) + 1
        if counts[cid] % 6 == 0:
            await safe_react(c.bot, u.effective_chat.id, u.message.message_id)
        
        text = (u.message.text or u.message.caption or "").strip()
        text_low = text.lower()
        
        media_m = _MEDIA_RE.search(text)
        if media_m:
            asyncio.create_task(download_and_send(u, c, media_m.group(0)))
            
        bot_username = bot_status.get("username", "")
        
        # Trigger conditions for AI — "beluga" in text, @mention, or reply to bot
        contains_beluga   = "beluga" in text_low
        contains_username = bool(bot_username) and (bot_username in text_low or f"@{bot_username}" in text_low)
        is_reply_to_bot   = (
            u.message.reply_to_message is not None
            and u.message.reply_to_message.from_user is not None
            and u.message.reply_to_message.from_user.id == c.bot.id
        )

        if text and (contains_beluga or contains_username or is_reply_to_bot):
            # ── FIX 3: Fully isolated AI reply block — each step has its own guard ──
            # Step 1: typing indicator (failure is non-fatal, never blocks the reply)
            try:
                await asyncio.wait_for(
                    c.bot.send_chat_action(u.effective_chat.id, "typing"),
                    timeout=4.0
                )
            except Exception:
                pass  # typing indicator is cosmetic — always continue to reply

            # Step 2: emoji reaction (non-fatal)
            emoji = "😼"
            try:
                emoji = await ai_emoji(text)
            except Exception:
                pass
            try:
                await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
            except Exception:
                pass

            # Step 3: AI reply — this MUST always execute and always send something
            reply = "Meow! 🐾"
            try:
                reply = await ai(CHAT_PROMPT, text, "Meow! 🐾")
            except Exception as e:
                logger.error(f"[monitor/ai] AI call failed: {e}")
                # reply stays as fallback "Meow! 🐾"

            try:
                await u.message.reply_text(reply)
            except Exception as e:
                logger.error(f"[monitor/reply] Failed to send reply: {e}")

        bot_status["message_count"] += 1
        bot_status["last_update"] = datetime.now()
    except Exception as e:
        logger.error(f"[monitor] {e}", exc_info=True)
        bot_status["error_count"] += 1

# ══════════════════════════════════════════════════════
#  FIX 4: ADVANCED GLOBAL ERROR HANDLER
# ══════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error

    # ── Silently ignore transient / expected Telegram errors ──
    if isinstance(err, (NetworkError, TimedOut)):
        logger.debug(f"[ErrHandler] Transient network error (ignored): {err}")
        return
    if isinstance(err, RetryAfter):
        logger.warning(f"[ErrHandler] Rate limited — retrying after {err.retry_after}s")
        await asyncio.sleep(err.retry_after + 1)
        return
    if isinstance(err, Forbidden):
        logger.debug(f"[ErrHandler] Forbidden (bot blocked or kicked): {err}")
        return
    if isinstance(err, BadRequest):
        # Ignore "message is not modified" and other harmless BadRequests
        msg = str(err).lower()
        if any(x in msg for x in ["not modified", "message to edit not found",
                                   "query is too old", "message can't be deleted"]):
            logger.debug(f"[ErrHandler] Benign BadRequest (ignored): {err}")
            return
        logger.warning(f"[ErrHandler] BadRequest: {err}")
        return
    if isinstance(err, InvalidToken):
        logger.critical("[ErrHandler] ❌ InvalidToken — shutting down")
        bot_status["running"] = False
        return

    # ── Log all unexpected errors with full traceback ──
    bot_status["error_count"] += 1
    tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__))

    # Determine context info for the log
    update_info = ""
    if isinstance(update, Update):
        chat = update.effective_chat
        user = update.effective_user
        update_info = (
            f" | update_id={update.update_id}"
            f" | chat={chat.id if chat else 'N/A'}({getattr(chat, 'type', '?')})"
            f" | user={user.id if user else 'N/A'}(@{getattr(user, 'username', '?')})"
        )

    logger.error(
        f"[ErrHandler] Unhandled exception{update_info}\n"
        f"Handler: {context.error.__class__.__name__}: {err}\n"
        f"Traceback:\n{tb_str}"
    )

    # ── Notify the owner via DM for critical unexpected errors ──
    if OWNER_ID:
        try:
            short_tb = tb_str[-800:] if len(tb_str) > 800 else tb_str
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(
                    f"⚠️ *Beluga Bot Error*\n\n"
                    f"`{err.__class__.__name__}: {str(err)[:200]}`\n\n"
                    f"```\n{short_tb}\n```"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass  # Never let the error handler itself crash

# ══════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════
async def main():
    logger.info("🐱  BELUGA BOT  v7.4.0  — PRODUCTION")
    
    github_load_db()
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)
    
    app = TGApp.builder().token(BOT_TOKEN).build()
    
    # Handlers
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
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(ttt_ready_callback, pattern=r"^ttt_ready:"))
    app.add_handler(CallbackQueryHandler(ttt_callback, pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(gm_callback, pattern=r"^gm:"))
    app.add_handler(CallbackQueryHandler(mine_callback, pattern=r"^mine:"))
    
    # Polls & Messages
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    
    app.add_error_handler(error_handler)
    
    await app.initialize()
    await app.start()
    
    try:
        me = await app.bot.get_me()
        bot_status["username"] = me.username.lower()
        logger.info(f"🤖 Connected Frame: @{me.username}")
    except Exception as e:
        logger.warning(f"[Startup Cache Error] {e}")

    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    bot_status["running"] = True
    
    stop_evt = asyncio.Event()
    try:
        import signal
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT, stop_evt.set)
    except (NotImplementedError, RuntimeError): pass
    
    async def periodic_cleanup():
        while not stop_evt.is_set():
            await asyncio.sleep(60)
            await cleanup_expired_games()
    
    cleanup_task = asyncio.create_task(periodic_cleanup())
    sync_task    = asyncio.create_task(periodic_github_sync())
    
    await stop_evt.wait()
    
    cleanup_task.cancel()
    sync_task.cancel()
    if db_needs_sync:
        github_sync_db()
        
    bot_status["running"] = False
    
    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try: await fn()
        except Exception: pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception as e:
        sys.exit(1)
