import os, logging, random, json, asyncio, requests, re
import urllib.parse, traceback, sys, hashlib, time
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
ttt_games: dict[str, dict] = {}
user_in_game: dict[str, str] = {}
game_timers: dict[str, dict] = {}

GAME_TIMEOUT = 300
TIMER_DURATION = 60

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
GIST_FILENAME = "beluga_leaderboard.json"
_gist_id_cache = None

def github_create_gist() -> Optional[str]:
    """Create new GitHub Gist for leaderboard"""
    if not GITHUB_TOKEN:
        logger.warning("[GitHub] No GITHUB_TOKEN set")
        return None
    try:
        r = requests.post(
            "https://api.github.com/gists",
            headers={"Authorization": f"token {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github.v3+json"},
            json={
                "description": "Beluga Bot Leaderboard - Auto created",
                "public": False,
                "files": {GIST_FILENAME: {
                    "content": json.dumps(db.get("scores", {}), indent=2)
                }}
            },
            timeout=10)
        if r.status_code == 201:
            gist_id = r.json().get("id")
            logger.info(f"✅ Created new GitHub Gist: {gist_id}")
            logger.info(f"📌 Add to .env: GITHUB_GIST_ID={gist_id}")
            return gist_id
    except Exception as e:
        logger.error(f"[GitHub Create] {e}")
    return None

def github_load_scores() -> dict:
    """Load leaderboard from GitHub Gist"""
    if not GITHUB_TOKEN:
        logger.warning("[GitHub] No GITHUB_TOKEN - leaderboard won't persist")
        return {}
    
    gist_id = GITHUB_GIST
    if not gist_id:
        logger.debug("[GitHub] No GIST_ID set, will create on first save")
        return {}
    
    try:
        r = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github.v3+json"},
            timeout=10)
        if r.status_code == 200:
            files = r.json().get("files", {})
            if GIST_FILENAME in files:
                scores = json.loads(files[GIST_FILENAME].get("content", "{}"))
                logger.info(f"✅ Loaded leaderboard from GitHub ({len(scores)} chats)")
                return scores
        elif r.status_code == 404:
            logger.warning("[GitHub] Gist not found (404)")
    except Exception as e:
        logger.error(f"[GitHub Load] {e}")
    return {}

def github_save_scores() -> bool:
    """Save leaderboard to GitHub Gist (or create if doesn't exist)"""
    if not GITHUB_TOKEN:
        logger.debug("[GitHub] No token, skipping save")
        return False
    
    gist_id = GITHUB_GIST
    
    # If no gist ID, try to create one
    if not gist_id:
        logger.info("[GitHub] Creating new Gist...")
        gist_id = github_create_gist()
        if not gist_id:
            logger.error("[GitHub] Failed to create Gist")
            return False
    
    try:
        r = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github.v3+json"},
            json={"files": {GIST_FILENAME: {
                "content": json.dumps(db.get("scores", {}), indent=2)
            }}},
            timeout=10)
        
        if r.status_code == 200:
            logger.debug(f"✅ Leaderboard saved to GitHub")
            return True
        else:
            logger.warning(f"[GitHub] Save failed: {r.status_code}")
            return False
    except Exception as e:
        logger.error(f"[GitHub Save] {e}")
    return False

async def async_github_save():
    """Async wrapper for GitHub save"""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, github_save_scores)
    except Exception as e:
        logger.debug(f"[Async GitHub] {e}")

def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    """Update score in memory and trigger GitHub save"""
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {"name": name, "score": 0})
    e["name"]  = name
    e["score"] = max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    save_db()
    return e["score"]

def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

# ══════════════════════════════════════════
#  HTTP SERVER
# ══════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({
        "status": "healthy", "uptime_seconds": up,
        "running": bot_status["running"],
        "messages": bot_status["message_count"],
        "version": "5.1.2",
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
WIKI_UA = {"User-Agent": "BelugaBot/5.1"}
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
#  /search
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
        asyncio.create_task(async_github_save())
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
#  /pump  /dump (OWNER ONLY)
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
        asyncio.create_task(async_github_save())
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
    if gkey in ttt_games: return True
    release_player(uid)
    return False

async def cleanup_expired_games():
    now = time.time()
    for gkey in list(ttt_games.keys()):
        g = ttt_games[gkey]
        if now - g.get("created", now) > GAME_TIMEOUT:
            for uid in [str(g.get("x_id","")), str(g.get("o_id",""))]:
                release_player(uid)
            if gkey in game_timers:
                del game_timers[gkey]
            del ttt_games[gkey]

# ══════════════════════════════════════════
#  TIMER MANAGEMENT
# ══════════════════════════════════════════
async def update_game_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    """Update timer every 3 seconds"""
    try:
        g = ttt_games.get(gkey)
        timer_data = game_timers.get(gkey)
        
        if not g or not timer_data:
            return
        
        cid = g["chat_id"]
        msg_id = int(gkey.split(":")[1])
        
        timer_data["remaining"] -= 3
        
        if timer_data["remaining"] <= 0:
            g["status"] = "timeout"
            winner_name = g["x_name"] if g["turn"] == "O" else g["o_name"]
            g["winner_name"] = winner_name
            
            text = ttt_build_text(g)
            kbd = ttt_build_keyboard(g["board"], disabled=True)
            
            try:
                await c.bot.edit_message_text(
                    text=text,
                    chat_id=cid,
                    message_id=msg_id,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kbd
                )
            except Exception:
                pass
            
            cid_s = str(cid)
            if not g["vs_bot"]:
                new_sc = update_score(cid_s, str(g["x_id"] if g["turn"] == "O" else g["o_id"]), winner_name, +20)
                asyncio.create_task(async_github_save())
            
            release_player(str(g["x_id"]))
            release_player(str(g["o_id"]))
            if gkey in game_timers:
                del game_timers[gkey]
            del ttt_games[gkey]
        else:
            text = ttt_build_text(g)
            kbd = ttt_build_keyboard(g["board"])
            
            try:
                await c.bot.edit_message_text(
                    text=text,
                    chat_id=cid,
                    message_id=msg_id,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kbd
                )
            except Exception:
                pass
            
            await asyncio.sleep(3)
            await update_game_timer(c, gkey)
    
    except Exception as e:
        logger.debug(f"[Timer] {e}")

# ══════════════════════════════════════════
#  TIC TAC TOE
# ══════════════════════════════════════════
TTT_EMPTY = "⬜"
TTT_X     = "❌"
TTT_O     = "⭕"

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
    turn   = g["turn"]
    status = g.get("status","playing")
    gkey = f"{g['chat_id']}:{g.get('msg_id','')}"
    timer_data = game_timers.get(gkey, {})
    remaining = timer_data.get("remaining", TIMER_DURATION)
    timer_str = f"{remaining//60:02d}:{remaining%60:02d}"

    if status == "playing":
        cur_name   = x_name if turn == "X" else o_name
        status_line = f"🎯 {cur_name}'s Turn\n⏱ {timer_str}"
    elif status == "timeout":
        winner_name = g.get("winner_name","")
        status_line = f"⏰ Time Expired!\n\n🏆 {winner_name} Wins by Timeout! +20 pts"
    elif status == "draw":
        status_line = "🤝 Match Draw!"
    else:
        winner_name = g.get("winner_name","")
        status_line = f"🏆 {winner_name} Wins! +20 pts"

    board = g["board"]
    rows  = []
    for row in range(3):
        rows.append("  ".join(board[row*3+col] for col in range(3)))
    board_str = "\n".join(rows)

    return (
        f"🎮 *TIC TAC TOE*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ {x_name}  🆚  ⭕ {o_name}\n"
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
        uid_a     = str(user_a.id)
        name_a    = (user_a.first_name or "Player")[:20]
        vs_bot    = True
        user_b_id = None
        name_b    = "🤖 Beluga Bot"

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
            "msg_id":    None,
        }

        kbd  = ttt_build_keyboard(board)
        text = ttt_build_text(g)
        msg  = await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                          reply_markup=kbd)

        g["msg_id"] = msg.message_id
        gkey = game_key(msg.message_id, cid)
        ttt_games[gkey] = g
        
        game_timers[gkey] = {"remaining": TIMER_DURATION}
        
        register_player(uid_a, gkey)
        if not vs_bot:
            register_player(str(user_b_id), gkey)
        
        asyncio.create_task(update_game_timer(c, gkey))

        bot_status["message_count"] += 1

    except Exception as e:
        logger.error(f"[tictac] {e}", exc_info=True)

async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        await q.answer()
        data   = q.data
        parts  = data.split(":")
        if len(parts) != 3 or parts[0] != "ttt": return
        action = parts[1]
        idx    = int(parts[2])

        cid    = q.message.chat_id
        mid    = q.message.message_id
        gkey   = game_key(mid, cid)
        g      = ttt_games.get(gkey)

        if not g:
            await q.answer("⏰ Game expired.", show_alert=True); return

        uid    = str(q.from_user.id)
        valid_x = uid == str(g["x_id"])
        valid_o = (uid == str(g["o_id"])) or (g["vs_bot"] and valid_x)

        if g["status"] != "playing":
            await q.answer("Game already ended!", show_alert=True); return

        if action == "noop":
            await q.answer("Cell taken!", show_alert=True); return

        if g["turn"] == "X" and not valid_x:
            if uid not in [str(g["x_id"]), str(g["o_id"])]:
                await q.answer("❌ Not in game!", show_alert=True)
            else:
                await q.answer("Not your turn!", show_alert=True)
            return
        if g["turn"] == "O" and not g["vs_bot"] and uid != str(g["o_id"]):
            if uid not in [str(g["x_id"]), str(g["o_id"])]:
                await q.answer("❌ Not in game!", show_alert=True)
            else:
                await q.answer("Not your turn!", show_alert=True)
            return

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
            release_player(str(g["x_id"]))
            release_player(str(g["o_id"]))
            if gkey in game_timers:
                del game_timers[gkey]
            del ttt_games[gkey]
            return

        if ttt_is_draw(board):
            g["status"] = "draw"
            text = ttt_build_text(g)
            kbd  = ttt_build_keyboard(board, disabled=True)
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)
            release_player(str(g["x_id"]))
            release_player(str(g["o_id"]))
            if gkey in game_timers:
                del game_timers[gkey]
            del ttt_games[gkey]
            return

        g["turn"] = "O" if g["turn"] == "X" else "X"

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
                    if gkey in game_timers:
                        del game_timers[gkey]
                    del ttt_games[gkey]
                    return
                if ttt_is_draw(board):
                    g["status"] = "draw"
                    text = ttt_build_text(g)
                    kbd  = ttt_build_keyboard(board, disabled=True)
                    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)
                    release_player(str(g["x_id"]))
                    if gkey in game_timers:
                        del game_timers[gkey]
                    del ttt_games[gkey]
                    return
                g["turn"] = "X"

        text = ttt_build_text(g)
        kbd  = ttt_build_keyboard(board)
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kbd)

    except Exception as e:
        logger.error(f"[ttt_callback] {e}", exc_info=True)

# ══════════════════════════════════════════
#  FUN COMMANDS
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
        text = (
            "🧠 *SMART BELUGA BOT*\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "«🌊 Welcome aboard,\n\n"
            "I'm your smart all-in-one assistant built to make your Telegram experience faster, easier and more fun.\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⚡ *Features*\n\n"
            "🎮 Games\n"
            "└ Play Tic Tac Toe and more\n\n"
            "📚 Learning\n"
            "└ Smart search & trivia\n\n"
            "🏆 Leaderboard\n"
            "└ Earn points and climb ranks\n\n"
            "🤖 Smart Utilities\n"
            "└ Fast tools and useful commands\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🎯 *Quick Commands*\n\n"
            "/tictac — Start Tic Tac Toe\n"
            "/quiz — Play trivia\n"
            "/search — Smart search\n"
            "/lb — Leaderboard\n"
            "/help — All commands\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🔥 Ready to begin?"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[start] {e}", exc_info=True)

async def help_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        text = (
            "📚 *ALL COMMANDS*\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🎮 *GAMES*\n"
            "/tictac — Tic Tac Toe\n\n"
            "🎓 *UTILITIES*\n"
            "/search — Smart search\n"
            "/quiz — Trivia quiz\n"
            "/lb — Leaderboard\n\n"
            "🎉 *FUN*\n"
            "/gay — Random funny message\n"
            "/couple — Random couple message\n\n"
            "💬 Mention 'beluga' for AI chat!"
        )
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[help] {e}", exc_info=True)

# ══════════════════════════════════════════
#  MONITOR
# ══════════════════════════════════════════
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    try:
        uid  = u.effective_user.id
        cid  = str(u.effective_chat.id)
        now  = datetime.now()

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
    logger.info("🐱  BELUGA BOT  v5.1.2")
    logger.info(f"   PORT={HTTP_PORT}  OWNER={OWNER_ID}")
    logger.info(f"   GitHub={'✅' if GITHUB_TOKEN else '❌'}")
    logger.info("=" * 55)

    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    loop = asyncio.get_running_loop()
    if GITHUB_TOKEN:
        try:
            gh = await asyncio.wait_for(
                loop.run_in_executor(None, github_load_scores), timeout=15)
            if gh: 
                db["scores"] = gh
                save_db()
                logger.info(f"✅ Loaded {len(gh)} chat leaderboards from GitHub")
        except Exception as e:
            logger.warning(f"[GitHub Load] {e}")
    else:
        logger.warning("[GitHub] No token - leaderboard won't persist across restarts")

    app = TGApp.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",              start_handler))
    app.add_handler(CommandHandler("help",               help_handler))
    app.add_handler(CommandHandler("search",             search_handler))
    app.add_handler(CommandHandler("quiz",               quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler(["gay","couple"],     fun_dispatcher))
    app.add_handler(CommandHandler(["pump","dump"],      pump_dump_handler))
    app.add_handler(CommandHandler("tictac",             tictac_handler))
    app.add_handler(CallbackQueryHandler(ttt_callback,   pattern=r"^ttt:"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)
    logger.info("✅ Handlers registered")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES)
    bot_status["running"] = True
    logger.info("✅ Beluga v5.1.2 is LIVE 🐱")

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

    cleanup_task.cancel()
    bot_status["running"] = False
    logger.info("🔄 Shutdown…")
    if GITHUB_TOKEN:
        try: 
            await loop.run_in_executor(None, github_save_scores)
            logger.info("✅ Saved leaderboard to GitHub")
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
