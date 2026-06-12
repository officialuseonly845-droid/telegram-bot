import os, logging, random, json, asyncio, requests, re, urllib.parse, traceback, sys, hashlib, time, base64, io
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import web
import aiohttp
from bs4 import BeautifulSoup
from telegram import Update, ReactionTypeEmoji, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, PollAnswerHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest, RetryAfter, InvalidToken
import pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import ccxt
import feedparser, qrcode, cv2
from PIL import Image, ImageDraw, ImageFont
from langdetect import detect
from textblob import TextBlob
from rapidfuzz import process, fuzz

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("Beluga")

# ═══════════════════════════════════════════════════════════════
# ENVIRONMENT & CONFIG
# ═══════════════════════════════════════════════════════════════
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
HTTP_PORT = int(os.environ.get("PORT", "10000"))
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN missing")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# STATE & CACHE
# ═══════════════════════════════════════════════════════════════
bot_status = {"running": False, "start_time": datetime.now(), "last_update": datetime.now(), "message_count": 0, "error_count": 0, "api_calls": 0, "failed_apis": 0, "username": ""}
quiz_cooldown, active_polls, spam_tracker = {}, {}, {}
db = {"scores": {}, "weekly": {}, "locks": {}, "seen": {}, "counts": {}}
fun_db = {"users": {}, "gay_couple_log": {}, "chat_memory": {}}
ttt_games, mine_games, user_in_game, game_timers, mine_timers, gm_tracker, gm_msg_lock = {}, {}, {}, {}, {}, {}, {}
sticker_file_ids, mine_play_stats = [], {}
db_needs_sync_groups, loaded_groups, fun_db_loaded = set(), set(), False
fun_db_needs_sync = False

# Watermark session state: maps user_id -> {gkey, step, font_size, color, style}
wm_sessions = {}

fun_cache = {"gay": {}, "couple": {}}
fun_cache_lock = asyncio.Lock()

exchange_cache = {"binance": None, "bybit": None, "okx": None, "bitget": None, "kraken": None}
cache_ticker = {}
cache_movers = {"ts": 0, "data": {}}
news_cache = {"crypto": {"ts": 0, "data": []}, "ai": {"ts": 0, "data": []}, "tech": {"ts": 0, "data": []}}

LB_IMAGE_URL = "https://i.postimg.cc/P5THW6RQ/file-00000000bce4720b905dc2e04c58fa80.png"
MINE_IMAGE_URL = "https://i.postimg.cc/hjCftW5b/file-0000000079a071fa95971d3b70015fc0.png"
GM_IMAGE_URL = "https://i.postimg.cc/Fs1h0CPs/file-000000001d7872078a894cdf6f6247c9.png"
UPDATES_CHANNEL = "https://t.me/BELUGAPY"

CHAT_PROMPT = """You are Beluga, a cute female AI cat assistant from @BELUGAPY channel. Stay in character.
Personality: warm, playful, intelligent, helpful. Reply in EXACTLY 2 short lines maximum.
Always use the user's first name when replying. Be casual and friendly.
DO NOT mention Team Oldy Crypto. You are from @BELUGAPY.
Reply in English always, even if user writes in another language. For Hinglish users prefer English.
Never use NLP analysis labels. Just reply naturally."""

BANANA_PROMPT = """You are Beluga from @BELUGAPY answering using web search results. Be concise, accurate, conversational.
Answer in English only. Summarize relevant facts directly. Don't say you searched. Just answer naturally as Beluga would.
Keep it to 3-4 lines max."""

QUIZ_TOPICS = ["deep ocean biology","quantum mechanics","human brain","solar system","animal behaviour","black holes","DNA genetics","ancient Egypt","World War 2"]
FALLBACK_QS = [
    {"q":"Which planet has most moons?","opts":["Jupiter","Saturn","Uranus","Neptune"],"ans":1,"fact":"Saturn: 146 moons!"},
    {"q":"What covers 71% of Earth?","opts":["Land","Ice","Water","Air"],"ans":2,"fact":"Oceans!"},
]
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
WIKI_UA = {"User-Agent": "BelugaBot/11.0"}
G_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "en-US,en;q=0.9"}

SENTIMENT_POSITIVE = ["😊", "😄", "❤️", "🔥", "✨", "🎉", "💖", "😻", "👍"]
SENTIMENT_NEGATIVE = ["😢", "😠", "💔", "😤", "😭", "😞", "😿", "😡", "⚠️"]
SENTIMENT_NEUTRAL = ["🤔", "😐", "👀", "🐾", "🎯", "📌", "💭", "🤷"]

# Watermark font styles
WM_STYLES = {
    "Normal": None,
    "Bold Classic": "bold",
    "Italic Elegant": "italic",
    "Condensed": "condensed",
    "Light Thin": "light",
    "Script Fancy": "script",
    "Block Strong": "block"
}

WM_COLORS_HEX = {
    "Red": (255, 0, 0),
    "Blue": (0, 0, 255),
    "Green": (0, 200, 0),
    "Yellow": (255, 255, 0),
    "White": (255, 255, 255),
    "Black": (0, 0, 0),
    "Violet": (148, 0, 211),
    "Indigo": (75, 0, 130),
    "Blue2": (0, 0, 255),
    "Green2": (0, 255, 0),
    "Yellow2": (255, 255, 0),
    "Orange": (255, 165, 0),
    "Red2": (255, 0, 0),
}

VIBGYOR_COLORS = {
    "🟣 Violet": (148, 0, 211, 200),
    "🔵 Indigo": (75, 0, 130, 200),
    "🔷 Blue": (0, 0, 255, 200),
    "🟢 Green": (0, 200, 0, 200),
    "🟡 Yellow": (255, 255, 0, 200),
    "🟠 Orange": (255, 165, 0, 200),
    "🔴 Red": (255, 0, 0, 200),
    "⚪ White": (255, 255, 255, 220),
    "⚫ Black": (0, 0, 0, 220),
}

def get_exchange(prefer: str = "bybit"):
    exchanges = ["bybit", "okx", "bitget", "kraken", "binance"]
    if prefer in exchanges:
        exchanges.remove(prefer)
        exchanges.insert(0, prefer)
    for ex_name in exchanges:
        try:
            if ex_name == "binance":
                ex = ccxt.binance({'enableRateLimit': True, 'timeout': 12000})
            elif ex_name == "bybit":
                ex = ccxt.bybit({'enableRateLimit': True, 'timeout': 12000})
            elif ex_name == "okx":
                ex = ccxt.okx({'enableRateLimit': True, 'timeout': 12000})
            elif ex_name == "bitget":
                ex = ccxt.bitget({'enableRateLimit': True, 'timeout': 12000})
            elif ex_name == "kraken":
                ex = ccxt.kraken({'enableRateLimit': True, 'timeout': 12000})
            else:
                continue
            ex.load_markets()
            logger.info(f"✅ Exchange: {ex_name}")
            return ex
        except Exception as e:
            logger.warning(f"⚠️ {ex_name} failed: {str(e)[:60]}")
            continue
    logger.error("❌ No exchange available")
    return None

exchange = get_exchange()

# ═══════════════════════════════════════════════════════════════
# GITHUB PERSISTENCE
# ═══════════════════════════════════════════════════════════════
def gh_rw(action: str, fname: str, data: dict = None, is_list: bool = False) -> any:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return data if data else ([] if is_list else {})
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{fname}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        if action == "read":
            r = requests.get(url + f"?ref={GITHUB_BRANCH}", headers=headers, timeout=10)
            if r.status_code == 200:
                return json.loads(base64.b64decode(r.json().get("content", "")).decode("utf-8"))
        elif action == "write":
            sha = None
            try:
                r = requests.get(url + f"?ref={GITHUB_BRANCH}", headers=headers, timeout=10)
                if r.status_code == 200:
                    sha = r.json().get("sha")
            except:
                pass
            content_b64 = base64.b64encode(json.dumps(data, indent=2, sort_keys=True).encode("utf-8")).decode("utf-8")
            payload = {"message": f"Update {fname} [skip ci]", "content": content_b64, "branch": GITHUB_BRANCH}
            if sha:
                payload["sha"] = sha
            requests.put(url, headers=headers, json=payload, timeout=15)
            return True
    except Exception as e:
        logger.error(f"[GitHub {action}] {e}")
    return data if data else ([] if is_list else {})

async def check_and_load_group(cid: str):
    if cid in loaded_groups:
        return
    loaded_groups.add(cid)
    loop = asyncio.get_running_loop()
    d = await loop.run_in_executor(None, gh_rw, "read", f"beluga_{cid}.json", {})
    if d:
        db.setdefault("scores", {})[cid] = d.get("scores", {})
        db.setdefault("weekly", {})[cid] = d.get("weekly", {})
        db.setdefault("locks", {})[cid] = d.get("locks", {})

async def check_and_load_fun_db():
    global fun_db, fun_db_loaded
    if fun_db_loaded:
        return
    fun_db_loaded = True
    loop = asyncio.get_running_loop()
    d = await loop.run_in_executor(None, gh_rw, "read", "beluga_fun.json", fun_db)
    if d:
        fun_db = d

async def periodic_github_sync():
    global fun_db_needs_sync
    while True:
        await asyncio.sleep(30)
        try:
            if db_needs_sync_groups:
                for cid in list(db_needs_sync_groups):
                    data = {
                        "scores": db.get("scores", {}).get(cid, {}),
                        "weekly": db.get("weekly", {}).get(cid, {}),
                        "locks": db.get("locks", {}).get(cid, {})
                    }
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, gh_rw, "write", f"beluga_{cid}.json", data)
                    db_needs_sync_groups.discard(cid)
            if fun_db_needs_sync:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, gh_rw, "write", "beluga_fun.json", fun_db)
                fun_db_needs_sync = False
        except Exception as e:
            logger.error(f"[periodic_sync] {e}")

# ═══════════════════════════════════════════════════════════════
# SCORING & LEADERBOARD
# ═══════════════════════════════════════════════════════════════
async def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    await check_and_load_group(cid)
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {"name": name, "user_id": int(uid) if uid.lstrip("-").isdigit() else 0, "score": 0})
    e["name"], e["user_id"], e["score"] = name, int(uid) if uid.lstrip("-").isdigit() else 0, max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    db_needs_sync_groups.add(cid)
    return e["score"]

async def safe_react(bot, chat_id: int, msg_id: int, emoji: str = None):
    if not emoji:
        emoji = random.choice(["🐱","🐾","❤️","🔥","👍","😻","😼","😂","✨","👀"])
    try:
        await asyncio.wait_for(bot.set_message_reaction(chat_id=chat_id, message_id=msg_id, reaction=[ReactionTypeEmoji(emoji=emoji)]), timeout=5.0)
    except:
        pass

def clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-zA-Z#0-9]+;", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def q_hash(q: str) -> str:
    return hashlib.md5(q.lower().strip().encode()).hexdigest()[:12]

def game_key(msg_id: int, cid: int) -> str:
    return f"{cid}:{msg_id}"

def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

def get_user_name(user) -> str:
    """Safely get user's first name"""
    if user and user.first_name:
        return user.first_name
    if user and user.username:
        return user.username
    return "buddy"

# ═══════════════════════════════════════════════════════════════
# SENTIMENT ANALYSIS
# ═══════════════════════════════════════════════════════════════
def analyze_sentiment(text: str) -> tuple:
    try:
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity
        if polarity > 0.3:
            return polarity, random.choice(SENTIMENT_POSITIVE)
        elif polarity < -0.3:
            return polarity, random.choice(SENTIMENT_NEGATIVE)
        else:
            return polarity, random.choice(SENTIMENT_NEUTRAL)
    except:
        return 0.0, "🐾"

# ═══════════════════════════════════════════════════════════════
# ADVANCED CV - TRUE CONTENT DETECTION
# ═══════════════════════════════════════════════════════════════
def advanced_image_analysis(img_bytes: bytes) -> str:
    """Detect text, faces, objects and describe image content"""
    try:
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return "😿 Could not decode the image."

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        analysis_parts = []

        # ── TEXT DETECTION via MSER ──
        mser = cv2.MSER_create()
        regions, _ = mser.detectRegions(gray)
        text_score = len(regions)
        has_text = text_score > 30

        # ── FACE DETECTION ──
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        num_faces = len(faces) if hasattr(faces, '__len__') else 0

        # ── EDGE / OBJECT COMPLEXITY ──
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        num_contours = len(contours)

        # ── DOMINANT COLOR ──
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0], None, [180], [0, 180])
        dominant_hue = int(np.argmax(hist))
        hue_map = [(0,10,"Red"),(10,25,"Orange"),(25,35,"Yellow"),(35,60,"Green"),(60,90,"Cyan"),(90,120,"Blue"),(120,140,"Purple"),(140,170,"Pink")]
        color_name = "Mixed"
        for hmin, hmax, name in hue_map:
            if hmin <= dominant_hue <= hmax:
                color_name = name
                break

        # ── BRIGHTNESS ──
        brightness = np.mean(gray)
        if brightness > 200:
            bright_desc = "very bright / white background"
        elif brightness > 140:
            bright_desc = "well-lit"
        elif brightness > 80:
            bright_desc = "normal lighting"
        else:
            bright_desc = "dark / low-light"

        # ── BUILD DESCRIPTION ──
        desc = "👁️ *What I see in this image:*\n\n"

        if num_faces >= 2:
            desc += f"👥 *People:* {num_faces} faces detected — looks like a group photo or conversation scene\n"
        elif num_faces == 1:
            desc += "🧑 *People:* 1 person detected — could be a selfie or portrait\n"

        if has_text:
            desc += f"📝 *Text:* Appears to contain written text or symbols\n"

        # Object type guess based on contours + color
        if num_contours > 150:
            desc += f"🎨 *Scene:* Complex scene with many elements — possibly a graphic, meme, screenshot or busy photo\n"
        elif num_contours > 60:
            desc += f"📦 *Scene:* Multiple distinct objects visible\n"
        elif num_contours > 15:
            desc += f"🖼️ *Scene:* Simple scene with a few objects\n"
        else:
            desc += f"⬜ *Scene:* Minimal composition — possibly a plain image or icon\n"

        desc += f"🎨 *Dominant color:* {color_name}\n"
        desc += f"💡 *Lighting:* {bright_desc}\n"
        desc += f"📐 *Resolution:* {w}×{h}px\n"
        desc += "\n_🐾 Beluga Vision — for deeper analysis send me text about what you want to know!_"

        return desc

    except Exception as e:
        logger.error(f"[cv_analysis] {e}")
        return f"😿 Could not analyze this image. Try a clearer one!"

# ═══════════════════════════════════════════════════════════════
# AI & GROQ
# ═══════════════════════════════════════════════════════════════
async def _groq_async(system: str, user: str, max_tok: int = 200) -> Optional[str]:
    if not GROQ_KEY:
        return None
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
                json=payload,
                timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                if r.status == 200:
                    return (await r.json())["choices"][0]["message"]["content"].strip()
                bot_status["failed_apis"] += 1
    except:
        bot_status["failed_apis"] += 1
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 200) -> str:
    try:
        res = await asyncio.wait_for(_groq_async(system, user, max_tok), timeout=14)
        if res:
            return res
    except:
        pass
    return fallback

async def ai_emoji(text: str) -> str:
    try:
        res = await asyncio.wait_for(_groq_async("Output ONE emoji matching emotion. ONLY the emoji, nothing else.", f"Text: '{text[:60]}'", 10), timeout=6)
        if res:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", res)
            if found:
                return found[0][0]
    except:
        pass
    return "😼"

# ═══════════════════════════════════════════════════════════════
# CHAT MEMORY
# ═══════════════════════════════════════════════════════════════
async def save_chat_memory(cid: str, uid: str, name: str, message: str):
    global fun_db_needs_sync
    await check_and_load_fun_db()
    memory_key = f"{cid}:{uid}"
    if memory_key not in fun_db.get("chat_memory", {}):
        fun_db["chat_memory"][memory_key] = []
    fun_db["chat_memory"][memory_key].append({"time": datetime.now().isoformat(), "msg": message[:100], "name": name})
    if len(fun_db["chat_memory"][memory_key]) > 5:
        fun_db["chat_memory"][memory_key] = fun_db["chat_memory"][memory_key][-5:]
    fun_db_needs_sync = True

# ═══════════════════════════════════════════════════════════════
# CRYPTO COMMANDS
# ═══════════════════════════════════════════════════════════════
async def crypto_price_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not exchange:
        return
    try:
        ticker = (c.args[0].upper() if c.args else "BTC")
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "💰")
        sm = await u.message.reply_text(f"⚡ *Fetching {ticker}/USDT...*", parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        try:
            td = await loop.run_in_executor(None, exchange.fetch_ticker, f"{ticker}/USDT")
            price = td.get('last', 0.0)
            change = td.get('percentage', 0.0)
            vol = td.get('baseVolume', 0.0)
            high = td.get('high', 0.0)
            low = td.get('low', 0.0)
            sign = "🟩 +" if change >= 0 else "🟥 "
            res = (f"⚡ *{ticker}/USDT*\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"🏷 *Price*\n`{price:,.4f} USDT`\n\n"
                   f"📊 *24h Change*\n`{sign}{change:.2f}%`\n\n"
                   f"📈 *24h High*\n`{high:,.4f}`\n\n"
                   f"📉 *24h Low*\n`{low:,.4f}`\n\n"
                   f"🔄 *Volume*\n`{vol:,.2f} {ticker}`\n\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🐾 _via Beluga Quant Engine_")
            await sm.edit_text(res, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await sm.edit_text(f"😿 Error: `{str(e)[:60]}`")
            bot_status["error_count"] += 1
    except Exception as e:
        logger.error(f"[crypto_price] {e}")

async def crypto_movers_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        gainers_mode = "topgainers" in (u.message.text or "").lower()
        lbl = "Gainers" if gainers_mode else "Losers"
        sm = await u.message.reply_text(f"⚡ *Finding top {lbl.lower()}...*", parse_mode=ParseMode.MARKDOWN)

        if not exchange:
            await sm.edit_text("😿 Exchange unavailable right now.")
            return

        loop = asyncio.get_running_loop()
        now = time.time()

        if cache_movers["ts"] and (now - cache_movers["ts"]) < 60:
            tickers = cache_movers["data"]
        else:
            try:
                tickers = await asyncio.wait_for(loop.run_in_executor(None, exchange.fetch_tickers), timeout=20)
                cache_movers["ts"] = now
                cache_movers["data"] = tickers
            except Exception as e:
                await sm.edit_text(f"😿 Failed to fetch data: `{str(e)[:60]}`")
                return

        records = []
        for sym, t in tickers.items():
            if not sym.endswith("/USDT"):
                continue
            ch = t.get('percentage')
            pr = t.get('last')
            if ch is None or pr is None:
                continue
            records.append({"sym": sym.split("/")[0], "ch": float(ch), "price": float(pr)})

        if not records:
            await sm.edit_text("😿 No data available.")
            return

        records.sort(key=lambda x: x["ch"], reverse=gainers_mode)

        text = f"📊 *TOP 5 {lbl.upper()} (24H)*\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, r in enumerate(records[:5], 1):
            s = "🟩 +" if r["ch"] >= 0 else "🟥 "
            text += f"*{i}. {r['sym']}*\n"
            text += f"Price: `{r['price']:,.4f}` USDT\n"
            text += f"Change: `{s}{r['ch']:.2f}%`\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += "🐾 _via Beluga Quant Engine_"

        await sm.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[crypto_movers] {e}")
        try:
            await sm.edit_text(f"😿 Error: `{str(e)[:50]}`")
        except:
            pass

async def crypto_chart_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not exchange:
        return
    try:
        parts = u.message.text.split()
        ticker, timeframe = "BTC", "1h"
        if len(parts) >= 2:
            ticker = parts[1].upper()
        for tf in ["5m", "15m", "1h", "4h", "1d"]:
            if tf in u.message.text.lower():
                timeframe = tf
                break
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "📈")
        sm = await u.message.reply_text(f"📊 *Fetching {ticker} ({timeframe})...*", parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        try:
            ohlcv = await loop.run_in_executor(None, lambda: exchange.fetch_ohlcv(f"{ticker}/USDT", timeframe, limit=45))
            if not ohlcv:
                raise ValueError("Empty dataset")
            df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
            df.set_index('Timestamp', inplace=True)
            buf = io.BytesIO()
            mc = mpf.make_marketcolors(up='#00C48C', down='#ff3366', inherit=True)
            s = mpf.make_mpf_style(base_mpf_style='charles', marketcolors=mc, gridcolor='#222222', facecolor='#0d0d0d')
            def _plot():
                mpf.plot(df, type='candle', style=s, volume=True, savefig=dict(fname=buf, dpi=115, bbox_inches='tight'), figratio=(14,9))
            await loop.run_in_executor(None, _plot)
            buf.seek(0)
            await sm.delete()
            await u.message.reply_photo(photo=buf, caption=f"📊 *{ticker}/USDT* • `{timeframe}`\n🐾 _Rendered via Beluga._", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await sm.edit_text(f"😿 Error: `{str(e)[:60]}`")
    except Exception as e:
        logger.error(f"[crypto_chart] {e}")

# ═══════════════════════════════════════════════════════════════
# NEWS - FIXED: single headline, 20 lines summary, no repeat, image
# ═══════════════════════════════════════════════════════════════
def fetch_google_news(feed_type: str) -> list:
    feeds = {
        "crypto": "https://news.google.com/rss/search?q=cryptocurrency+bitcoin",
        "ai": "https://news.google.com/rss/search?q=artificial+intelligence+AI",
        "tech": "https://news.google.com/rss/search?q=technology+innovation"
    }
    url = feeds.get(feed_type, feeds["tech"])
    results = []
    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:20]:
            title = entry.get("title", "").strip()
            # Remove source suffix like " - TechCrunch" that Google adds
            title = re.sub(r'\s*-\s*[^-]{3,40}$', '', title).strip()
            link = entry.get("link", "#")
            pub_date = entry.get("published", "")[:16]

            summary_html = entry.get("summary", "")
            img_url = None
            img_match = re.search(r'src=["\'](https://[^"\']+\.(?:jpg|jpeg|png|webp|gif))["\']', summary_html, re.IGNORECASE)
            if img_match:
                img_url = img_match.group(1)

            # Fetch article content for 20 lines
            full_text = ""
            try:
                r = requests.get(link, headers=G_HDR, timeout=8)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    # Remove scripts/styles
                    for tag in soup(["script","style","nav","footer","header","aside"]):
                        tag.decompose()
                    paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 50]
                    full_text = " ".join(paragraphs[:8])[:1800]
                    # Try to get image from article page
                    if not img_url:
                        og_img = soup.find("meta", property="og:image")
                        if og_img and og_img.get("content"):
                            img_url = og_img["content"]
            except:
                pass

            summary_text = clean_html(summary_html)[:200] if not full_text else full_text[:1800]

            if title:
                results.append({
                    "title": title[:200],
                    "link": link,
                    "summary": summary_text,
                    "image": img_url,
                    "date": pub_date
                })
    except Exception as e:
        logger.error(f"[fetch_google_news] {e}")
    return results

def _format_news_body(summary: str) -> str:
    """Format summary into ~20 readable lines"""
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', summary.strip())
    lines = []
    for s in sentences:
        s = s.strip()
        if len(s) > 20:
            lines.append(s)
        if len(lines) >= 18:
            break
    return "\n".join(lines) if lines else summary[:800]

async def execute_news_flow(u: Update, c: ContextTypes.DEFAULT_TYPE, feed_type: str, label: str):
    if not u.message:
        return
    try:
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "📰")
        sm = await u.message.reply_text(f"🛰 *Fetching {label}...*", parse_mode=ParseMode.MARKDOWN)

        loop = asyncio.get_running_loop()
        now = time.time()

        if news_cache[feed_type]["ts"] and (now - news_cache[feed_type]["ts"]) < 300:
            items = news_cache[feed_type]["data"]
        else:
            items = await loop.run_in_executor(None, fetch_google_news, feed_type)
            news_cache[feed_type]["ts"] = now
            news_cache[feed_type]["data"] = items

        if not items:
            await sm.edit_text("😿 No news found right now.")
            return

        await sm.delete()

        # Pick a random article from top 8
        top = random.choice(items[:min(8, len(items))])
        body = _format_news_body(top["summary"])

        # Build caption — headline once only, then body, then readmore
        caption = (
            f"📰 *{label.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 *{top['title']}*\n\n"
            f"{body}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {top['date']}"
        )

        # Telegram caption limit is 1024 chars
        if len(caption) > 1020:
            caption = caption[:1017] + "..."

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📖 Read Full Article", url=top["link"])]])

        sent = False
        if top["image"]:
            try:
                await u.message.reply_photo(
                    photo=top["image"],
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kb,
                    reply_to_message_id=u.message.message_id
                )
                sent = True
            except Exception as img_err:
                logger.warning(f"[news_img] {img_err}")

        if not sent:
            # Send as text with longer body since no image limit
            full_cap = (
                f"📰 *{label.upper()}*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📌 *{top['title']}*\n\n"
                f"{body}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 {top['date']}"
            )
            await u.message.reply_text(
                full_cap,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb,
                reply_to_message_id=u.message.message_id
            )

        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[execute_news_flow] {e}")

# ═══════════════════════════════════════════════════════════════
# QR TOOLS
# ═══════════════════════════════════════════════════════════════
async def qr_generate_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text("🐱 Usage: `/qr text here`")
            return
        payload = parts[1].strip()
        sm = await u.message.reply_text("🟩 *Generating QR Code...*", parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        def _build():
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(payload)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white")
        img = await loop.run_in_executor(None, _build)
        bio = io.BytesIO()
        img.save(bio, "PNG")
        bio.seek(0)
        await sm.delete()
        await u.message.reply_photo(photo=bio, caption="🤖 *QR Code Generated.*\n🐾 _Via Beluga Tools._")
    except Exception as e:
        logger.error(f"[qr_generate] {e}")

async def qr_scan_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Reply to an image with `/scanqr`.")
        return
    try:
        sm = await u.message.reply_text("🟩 *Scanning QR Code...*", parse_mode=ParseMode.MARKDOWN)
        photo = u.message.reply_to_message.photo[-1]
        file_obj = await c.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file_obj.download_to_memory(buf)
        buf.seek(0)
        loop = asyncio.get_running_loop()
        def _decode():
            arr = np.frombuffer(buf.getvalue(), dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            detector = cv2.QRCodeDetector()
            val, _, _ = detector.detectAndDecode(img)
            return val
        decoded_text = await loop.run_in_executor(None, _decode)
        if decoded_text:
            await sm.edit_text(f"🤖 *Decoded:*\n```\n{decoded_text}\n```", parse_mode=ParseMode.MARKDOWN)
        else:
            await sm.edit_text("😿 QR data unreadable.")
    except Exception as e:
        logger.error(f"[qr_scan] {e}")

# ═══════════════════════════════════════════════════════════════
# IMAGE TOOLS (resize, compress, info)
# ═══════════════════════════════════════════════════════════════
async def img_handler(u: Update, c: ContextTypes.DEFAULT_TYPE, action: str):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Reply to a photo.")
        return
    try:
        sm = await u.message.reply_text(f"📦 *Processing image...*", parse_mode=ParseMode.MARKDOWN)
        p = u.message.reply_to_message.photo[-1]
        f = await c.bot.get_file(p.file_id)
        b = io.BytesIO()
        await f.download_to_memory(b)
        b.seek(0)
        loop = asyncio.get_running_loop()
        if action == "info":
            im = Image.open(b)
            await sm.edit_text(
                f"🖼 *Image Report*\n━━━━━━━━━━━━━━━━━━━━\n📐 *Resolution:* `{im.size[0]} x {im.size[1]} pixels`\n🎨 *Color Mode:* `{im.mode}`\n💾 *Size:* `{p.file_size / 1024:.2f} KB`\n━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.MARKDOWN
            )
        elif action == "resize":
            def _scale():
                im = Image.open(b)
                out = im.resize((512, 512), Image.Resampling.LANCZOS)
                out_b = io.BytesIO(); out.save(out_b, "PNG"); out_b.seek(0)
                return out_b
            res_b = await loop.run_in_executor(None, _scale)
            await sm.delete()
            await u.message.reply_photo(photo=res_b, caption="📐 *Resized to 512×512.*")
        elif action == "compress":
            def _crunch():
                im = Image.open(b)
                out_b = io.BytesIO(); im.save(out_b, "JPEG", quality=22); out_b.seek(0)
                return out_b
            res_b = await loop.run_in_executor(None, _crunch)
            await sm.delete()
            await u.message.reply_photo(photo=res_b, caption="💾 *Compressed.*")
    except Exception as e:
        logger.error(f"[img_handler] {e}")

# ═══════════════════════════════════════════════════════════════
# WATERMARK - FULL INTERACTIVE FLOW WITH INLINE KEYBOARD
# ═══════════════════════════════════════════════════════════════
async def watermark_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Step 1: User sends /watermark text replying to image → ask font size"""
    if not u.message:
        return
    if not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Reply to a photo with `/watermark your text`.")
        return

    parts = u.message.text.split(maxsplit=1)
    wm_text = parts[1].strip() if len(parts) > 1 else "BELUGAPY"

    uid = u.effective_user.id
    cid = u.effective_chat.id
    photo = u.message.reply_to_message.photo[-1]

    # Store session
    wm_sessions[uid] = {
        "text": wm_text,
        "file_id": photo.file_id,
        "chat_id": cid,
        "step": "font_size"
    }

    # Build font size keyboard
    sizes = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    rows = []
    row = []
    for sz in sizes:
        row.append(InlineKeyboardButton(str(sz), callback_data=f"wm:size:{uid}:{sz}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    await u.message.reply_text(
        f"🖊 *Watermark: `{wm_text}`*\n\nStep 1️⃣ — Choose *font size:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows)
    )

def _build_color_keyboard(uid: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for label in VIBGYOR_COLORS:
        row.append(InlineKeyboardButton(label, callback_data=f"wm:color:{uid}:{label}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def _build_style_keyboard(uid: int) -> InlineKeyboardMarkup:
    styles = ["Normal", "Bold Classic", "Italic Elegant", "Condensed", "Light Thin", "Script Fancy", "Block Strong"]
    rows = []
    for s in styles:
        rows.append([InlineKeyboardButton(s, callback_data=f"wm:style:{uid}:{s}")])
    return InlineKeyboardMarkup(rows)

async def watermark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
        parts = q.data.split(":", 3)
        _, step, owner_uid_str, value = parts
        owner_uid = int(owner_uid_str)

        # Only the original requester can interact
        if q.from_user.id != owner_uid:
            await q.answer("❌ This is not your watermark session!", show_alert=True)
            return

        sess = wm_sessions.get(owner_uid)
        if not sess:
            await q.edit_message_text("⏰ Session expired. Use /watermark again.")
            return

        if step == "size":
            sess["font_size"] = int(value)
            sess["step"] = "color"
            await q.edit_message_text(
                f"🖊 *Watermark: `{sess['text']}`*\nFont size: `{value}`\n\nStep 2️⃣ — Choose *text color:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_build_color_keyboard(owner_uid)
            )

        elif step == "color":
            sess["color"] = value
            sess["step"] = "style"
            await q.edit_message_text(
                f"🖊 *Watermark: `{sess['text']}`*\nFont size: `{sess['font_size']}` | Color: `{value}`\n\nStep 3️⃣ — Choose *text style:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_build_style_keyboard(owner_uid)
            )

        elif step == "style":
            sess["style"] = value
            sess["step"] = "done"
            await q.edit_message_text("⚙️ *Applying watermark...*", parse_mode=ParseMode.MARKDOWN)

            # Apply watermark
            try:
                file_obj = await context.bot.get_file(sess["file_id"])
                buf = io.BytesIO()
                await file_obj.download_to_memory(buf)
                buf.seek(0)

                font_size = sess.get("font_size", 40)
                color_key = sess.get("color", "⚪ White")
                style_name = sess.get("style", "Normal")
                wm_text = sess["text"]

                rgba = VIBGYOR_COLORS.get(color_key, (255, 255, 255, 200))

                loop = asyncio.get_running_loop()

                def _apply():
                    im = Image.open(buf).convert("RGBA")
                    txt_layer = Image.new("RGBA", im.size, (255, 255, 255, 0))
                    draw = ImageDraw.Draw(txt_layer)

                    # Try to load a font
                    font = None
                    try:
                        if style_name in ("Bold Classic", "Block Strong"):
                            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
                        elif style_name in ("Italic Elegant", "Script Fancy"):
                            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", font_size)
                        elif style_name == "Light Thin":
                            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf", font_size)
                        else:
                            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
                    except:
                        try:
                            font = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSans.ttf", font_size)
                        except:
                            font = ImageFont.load_default()

                    # Get text size for centering
                    try:
                        bbox = draw.textbbox((0, 0), wm_text, font=font)
                        tw = bbox[2] - bbox[0]
                        th = bbox[3] - bbox[1]
                    except:
                        tw, th = len(wm_text) * font_size // 2, font_size

                    # Position: bottom center
                    x = max(0, (im.size[0] - tw) // 2)
                    y = max(0, im.size[1] - th - 20)

                    # Shadow for visibility
                    shadow_color = (0, 0, 0, 120)
                    draw.text((x+2, y+2), wm_text, font=font, fill=shadow_color)
                    draw.text((x, y), wm_text, font=font, fill=rgba)

                    combined = Image.alpha_composite(im, txt_layer)
                    out_b = io.BytesIO()
                    combined.convert("RGB").save(out_b, "JPEG", quality=92)
                    out_b.seek(0)
                    return out_b

                res_b = await loop.run_in_executor(None, _apply)

                await context.bot.send_photo(
                    chat_id=sess["chat_id"],
                    photo=res_b,
                    caption=f"🛡 *Watermark Applied!*\n`{wm_text}` | Size: `{font_size}` | Color: `{color_key}` | Style: `{style_name}`",
                    parse_mode=ParseMode.MARKDOWN
                )
                wm_sessions.pop(owner_uid, None)

            except Exception as e:
                logger.error(f"[wm_apply] {e}")
                await q.edit_message_text(f"😿 Error applying watermark: `{str(e)[:60]}`", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"[wm_callback] {e}")

# ═══════════════════════════════════════════════════════════════
# COMPUTER VISION - IMAGE ANALYSIS ON QUESTION
# ═══════════════════════════════════════════════════════════════
async def analyze_image_with_cv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        return
    replied = update.message.reply_to_message
    has_media = bool(replied.photo or replied.sticker or replied.document)
    if not has_media:
        return

    text = (update.message.text or "").lower().strip()
    question_words = ["what","who","where","show","see","contains","display","in","describe","tell","whats","explain","is","are","text","written","says","this","that"]
    if not any(w in text for w in question_words) or len(text) < 3:
        return

    try:
        sm = await update.message.reply_text("👁️ *Analyzing...*", parse_mode=ParseMode.MARKDOWN)

        if replied.photo:
            file_obj = await context.bot.get_file(replied.photo[-1].file_id)
        elif replied.sticker:
            file_obj = await context.bot.get_file(replied.sticker.file_id)
        elif replied.document and replied.document.mime_type and replied.document.mime_type.startswith("image"):
            file_obj = await context.bot.get_file(replied.document.file_id)
        else:
            await sm.edit_text("😿 Unsupported media type.")
            return

        buf = io.BytesIO()
        await file_obj.download_to_memory(buf)
        buf.seek(0)
        img_bytes = buf.getvalue()

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, advanced_image_analysis, img_bytes)
        await sm.edit_text(result, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[analyze_image] {e}")

# ═══════════════════════════════════════════════════════════════
# QUIZ & GAMES
# ═══════════════════════════════════════════════════════════════
def quiz_on_cooldown(cid: str, question: str) -> bool:
    return time.time() < quiz_cooldown.get(cid, {}).get(q_hash(question), 0)

def mark_quiz(cid: str, question: str):
    quiz_cooldown.setdefault(cid, {})[q_hash(question)] = time.time() + 3600

async def gen_quiz(topic: str, cid: str) -> Optional[dict]:
    for _ in range(2):
        try:
            raw = await ai("Trivia master. Output ONLY raw JSON, no markdown.", f"Topic: '{topic}'. Generate 1 MC question.\n" + '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}', "", max_tok=200)
            if not raw:
                continue
            m = re.search(r"\{[\s\S]+\}", raw)
            if not m:
                continue
            d = json.loads(m.group(0))
            q, opts, idx, fact = str(d.get("question","")).strip(), d.get("options",[]), int(d.get("correct_index",0)), str(d.get("fun_fact","Meow!")).strip()
            if not q or len(opts) != 4 or not (0 <= idx <= 3):
                continue
            if quiz_on_cooldown(cid, q):
                continue
            return {"question":q,"options":opts,"correct_index":idx,"fun_fact":fact}
        except:
            pass
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        topic = (u.message.text.split(maxsplit=1)[1].strip() if len(u.message.text.split(maxsplit=1)) > 1 else None) or random.choice(QUIZ_TOPICS)
        cid, cid_i = str(u.effective_chat.id), u.effective_chat.id
        await safe_react(c.bot, cid_i, u.message.message_id, "💡")
        sm = await u.message.reply_text("🎲 *Generating quiz...*", parse_mode=ParseMode.MARKDOWN)
        qdata = await gen_quiz(topic, cid)
        try:
            await sm.delete()
        except:
            pass
        if qdata:
            mark_quiz(cid, qdata["question"])
            try:
                pm = await c.bot.send_poll(
                    chat_id=cid_i, question=f"🐱 {qdata['question'][:255]}",
                    options=[str(o)[:100] for o in qdata["options"]],
                    type="quiz", correct_option_id=qdata["correct_index"],
                    is_anonymous=False, explanation=qdata["fun_fact"][:200]
                )
                active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":qdata["correct_index"]}
                bot_status["message_count"] += 1
                return
            except:
                pass
        fb = random.choice(FALLBACK_QS)
        mark_quiz(cid, fb["q"])
        pm = await c.bot.send_poll(
            chat_id=cid_i, question=f"🐱 {fb['q']}",
            options=fb["opts"], type="quiz", correct_option_id=fb["ans"],
            is_anonymous=False, explanation=fb["fact"]
        )
        active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":fb["ans"]}
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[quiz] {e}")

async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        ans = u.poll_answer
        if not ans:
            return
        info = active_polls.get(ans.poll_id)
        if not info or not ans.option_ids or ans.option_ids[0] != info["correct_index"]:
            return
        cid, uid = str(info["chat_id"]), str(ans.user.id)
        name = (ans.user.first_name or "?")[:30]
        await update_score(cid, uid, name, +10)
    except:
        pass

# ═══════════════════════════════════════════════════════════════
# LEADERBOARD - ALWAYS FROM GITHUB FILE
# ═══════════════════════════════════════════════════════════════
async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid = str(u.effective_chat.id)
        # Always fresh from GitHub
        loop = asyncio.get_running_loop()
        gh_data = await loop.run_in_executor(None, gh_rw, "read", f"beluga_{cid}.json", {})

        if gh_data:
            # Update in-memory with latest from GitHub
            db.setdefault("scores", {})[cid] = gh_data.get("scores", {})
            db.setdefault("weekly", {})[cid] = gh_data.get("weekly", {})
            loaded_groups.add(cid)

        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)

        seen_ids = set()
        clean_lb = [e for e in lb if e.get("user_id") not in seen_ids and not seen_ids.add(e.get("user_id"))]

        lw = db.get("weekly", {}).get(cid, {})
        lines = []

        if lw and lw.get("top3"):
            lines.append("🏆 *LAST WEEK CHAMPIONS* 🏆\n")
            lines.extend([f"{MEDALS[i]} {e.get('name','?')[:18]} — {e.get('score',0):,} pts" for i, e in enumerate(lw["top3"])])
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n")

        lines += ["╔════════════════════════════╗", "🏆  *CURRENT LEADERBOARD*  🏆", "╚════════════════════════════╝\n"]

        if not clean_lb:
            lines.append("No scores yet! Play some games 🎮")
        else:
            for i, e in enumerate(clean_lb[:10]):
                m = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
                lines.append(f"{m} `{e.get('name','Unknown')[:18]:<18}` `{e.get('score',0):>6,} pts`")

        lines += ["\n━━━━━━━━━━━━━━━━━━━━", "➕ +10 quiz/ttt  ·  +700 mine  ·  +50 gm"]
        text = "\n".join(lines)

        try:
            await u.message.reply_photo(photo=LB_IMAGE_URL, caption=text, parse_mode=ParseMode.MARKDOWN)
        except:
            await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[lb] {e}")

async def nw_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only.")
            return
        cid = str(u.effective_chat.id)
        await check_and_load_group(cid)
        lb = sorted(db.get("scores",{}).get(cid,{}).values(), key=lambda x: x.get("score",0), reverse=True)
        seen_ids = set()
        clean_lb = [e for e in lb if e.get("user_id") not in seen_ids and not seen_ids.add(e.get("user_id"))]
        top3 = [{"name": e.get("name","?"), "score": e.get("score",0)} for e in clean_lb[:3]]
        wk_label = datetime.now().strftime("%d %b %Y")
        db.setdefault("weekly",{})[cid] = {"top3": top3, "week_label": wk_label}
        db["scores"][cid] = {}
        db_needs_sync_groups.add(cid)
        announce = ["🏆🎉 *NEW WEEK!* 🎉🏆", f"\n_Week: {wk_label}_\n", "👑 *Champions:*\n"]
        announce.extend([f"{MEDALS[i]} *{e['name']}* — {e['score']:,} pts" for i, e in enumerate(top3)])
        announce.extend(["\n🔄 *All scores reset!*", "🚀 _New battle begins!_"])
        await u.message.reply_text("\n".join(announce), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[nw] {e}")

async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only.")
            return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("⚠️ Reply to a user.")
            return
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await u.message.reply_text("⚠️ Usage: `/pump 100`")
            return
        amount = int(parts[1])
        cmd = parts[0].lstrip("/").lower().split("@")[0]
        delta = +amount if cmd == "pump" else -amount
        target, cid = u.message.reply_to_message.from_user, str(u.effective_chat.id)
        new_sc = await update_score(cid, str(target.id), (target.first_name or "User")[:30], delta)
        emoji = "🚀" if cmd == "pump" else "📉"
        sign = "+" if delta > 0 else ""
        await u.message.reply_text(
            f"{emoji} *{'PUMP' if cmd=='pump' else 'DUMP'}*\n\n👤 *{target.first_name}*\n{'📈' if delta>0 else '📉'} {sign}{amount:,} pts\n💰 New total: *{new_sc:,} pts*",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"[pump_dump] {e}")

# ═══════════════════════════════════════════════════════════════
# FUN COMMANDS
# ═══════════════════════════════════════════════════════════════
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global fun_db_needs_sync
    if not u.message:
        return
    try:
        cid = str(u.effective_chat.id)
        await check_and_load_group(cid)
        await check_and_load_fun_db()
        cmd = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        active_users = list(db.get("seen", {}).get(cid, {}).values())
        if len(active_users) < (2 if cmd == "couple" else 1):
            if OWNER_ID:
                active_users.append({"id": OWNER_ID, "un": "Owner", "n": "Owner"})
        if len(active_users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text("😿 Need more group members!")
            return
        day = datetime.now().strftime("%y-%m-%d")
        lk = f"{cid}:{cmd}:{day}"
        async with fun_cache_lock:
            if lk in fun_db["gay_couple_log"]:
                cached = fun_db["gay_couple_log"][lk]
                if cached.get("date") == day:
                    await u.message.reply_text(cached["result"], parse_mode=ParseMode.MARKDOWN)
                    return
        if cmd == "couple":
            m = random.sample(active_users, min(2, len(active_users)))
            res = f"💖 *{m[0]['n']}* 💞 *{m[1]['n']}*\n100% compatible!" if len(m) == 2 else f"💖 *{m[0]['n']}* needs a partner! 💔"
        else:
            m = [random.choice(active_users)]
            res = f"🌈 *{m[0]['n']}* is today's rainbow! 🌈"
        async with fun_cache_lock:
            fun_db["gay_couple_log"][lk] = {"date": day, "result": res, "users": [p.get("id") for p in m]}
            fun_db_needs_sync = True
        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[fun_dispatcher] {e}")

# ═══════════════════════════════════════════════════════════════
# GOOD MORNING
# ═══════════════════════════════════════════════════════════════
def _build_gm_caption(users: list, date_str: str) -> str:
    display_users = users[-15:] if len(users) > 15 else users
    lines = ["📸 *DAILY ATTENDANCE*\n", "🥱 Mark attendance!\n", f"📅 {date_str}  |  👥 {len(users)}\n", "━━━━━━━━━━━━━━━━━━━━\n"]
    if len(users) > 15:
        lines.append(f"... +{len(users)-15} more...\n")
    for i, user in enumerate(display_users, 1):
        lines.append(f"{i}. {user['name']} • {user['time']}")
    lines += ["\n━━━━━━━━━━━━━━━━━━━━\n", "🔥 +50 pts for check-in"]
    return "\n".join(lines)

async def gm_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only.")
            return
        cid = str(u.effective_chat.id)
        date_str = datetime.now().strftime("%d %b %Y")
        msg = None
        try:
            msg = await u.message.reply_photo(
                photo=GM_IMAGE_URL,
                caption=_build_gm_caption([], date_str),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]])
            )
        except:
            msg = await u.message.reply_text(
                text=_build_gm_caption([], date_str),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]])
            )
        if msg:
            gm_tracker[cid] = (msg.message_id, [], date_str)
            gm_msg_lock[cid] = asyncio.Lock()
    except Exception as e:
        logger.error(f"[gm] {e}")

async def gm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        parts = q.data.split(":")
        cid = parts[2]
        if cid not in gm_msg_lock:
            gm_msg_lock[cid] = asyncio.Lock()
        async with gm_msg_lock[cid]:
            if cid not in gm_tracker:
                await q.answer("⏰ Expired")
                return
            msg_id, users, date_str = gm_tracker[cid]
            user, user_id = q.from_user, str(q.from_user.id)
            if any(uu.get("id") == user_id for uu in users):
                await q.answer("✅ Already marked")
                return
            u_name = (user.first_name or "User")[:20]
            utime = datetime.now().strftime("%H:%M")
            users.append({"id": user_id, "name": u_name, "time": utime})
            gm_tracker[cid] = (msg_id, users, date_str)
            await update_score(str(q.message.chat_id), user_id, u_name, +50)
            try:
                new_cap = _build_gm_caption(users, date_str)
                if q.message.photo:
                    await context.bot.edit_message_caption(
                        chat_id=q.message.chat_id, message_id=msg_id, caption=new_cap,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]]),
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await context.bot.edit_message_text(
                        chat_id=q.message.chat_id, message_id=msg_id, text=new_cap,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]]),
                        parse_mode=ParseMode.MARKDOWN
                    )
                await q.answer(f"✅ +50 pts, {u_name}!")
            except:
                await q.answer("✅ Marked!")
    except Exception as e:
        logger.error(f"[gm_callback] {e}")

# ═══════════════════════════════════════════════════════════════
# TIC TAC TOE
# ═══════════════════════════════════════════════════════════════
TTT_EMPTY, TTT_X, TTT_O = "⬜", "❌", "⭕"
WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

def ttt_check_winner(board):
    for a,b,cc in WINS:
        if board[a] == board[b] == board[cc] and board[a] != TTT_EMPTY:
            return board[a]
    return None

def ttt_is_draw(board):
    return all(c != TTT_EMPTY for c in board) and not ttt_check_winner(board)

def _minimax(board, is_max, alpha, beta):
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

def ttt_bot_move(board):
    best_score, best_move = -1000, -1
    for i in range(9):
        if board[i] != TTT_EMPTY: continue
        board[i] = TTT_O
        score = _minimax(board, False, -1000, 1000)
        board[i] = TTT_EMPTY
        if score > best_score:
            best_score, best_move = score, i
    return best_move

def ttt_build_keyboard(board, disabled=False):
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx = row*3 + col
            cb = f"ttt:noop:{idx}" if (board[idx] != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            r.append(InlineKeyboardButton(board[idx], callback_data=cb))
        rows.append(r)
    return InlineKeyboardMarkup(rows)

def ttt_build_text(g):
    gkey = f"{g['chat_id']}:{g.get('msg_id','')}"
    rem = game_timers.get(gkey, {}).get("remaining", 300)
    tsec = f"{rem//60:02d}:{rem%60:02d}"
    board_str = "\n".join([" ".join(g["board"][r*3+col] for col in range(3)) for r in range(3)])
    status = g.get("status","playing")
    if status == "playing":
        sl = f"🎯 *{g['x_name'] if g['turn'] == 'X' else g['o_name']}'s turn* {'❌' if g['turn'] == 'X' else '⭕'}  ⏱ `{tsec}`"
    elif status == "timeout":
        sl = f"⏰ *Time up!*\n🏆 *{g.get('winner_name','')}* wins!"
    elif status == "draw":
        sl = "🤝 *Draw!*"
    else:
        sl = f"🏆 *{g.get('winner_name','')}* wins!"
    return f"🎮 *TIC TAC TOE*\n━━━━━━━━━━━━━━\n❌ {g['x_name']}  🆚  ⭕ {g['o_name']}\n━━━━━━━━━━━━━━\n\n{board_str}\n\n━━━━━━━━━━━━━━\n{sl}"

async def cleanup_expired_games():
    now = time.time()
    for gkey in list(ttt_games.keys()):
        g = ttt_games[gkey]
        if now - g.get("created", now) > 300:
            for uid in [str(g.get("x_id","")), str(g.get("o_id",""))]:
                user_in_game.pop(uid, None)
            game_timers.pop(gkey, None)
            del ttt_games[gkey]

async def run_game_timer(c, gkey):
    try:
        while True:
            await asyncio.sleep(5)
            g = ttt_games.get(gkey)
            td = game_timers.get(gkey)
            if not g or not td or g.get("status") != "playing": return
            td["remaining"] = max(0, td["remaining"] - 5)
            cid, msg_id = g.get("chat_id"), g.get("msg_id")
            if not msg_id: return
            if td["remaining"] <= 0:
                g["status"] = "timeout"
                g["winner_name"] = (g["o_name"] if g["turn"] == "X" else g["x_name"])
                try:
                    await c.bot.edit_message_text(chat_id=cid, message_id=msg_id, text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"], disabled=True))
                except: pass
                for uid in [str(g.get("x_id","")), str(g.get("o_id",""))]: user_in_game.pop(uid, None)
                game_timers.pop(gkey, None); ttt_games.pop(gkey, None)
                return
            try:
                await c.bot.edit_message_text(chat_id=cid, message_id=msg_id, text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"]))
            except: pass
    except asyncio.CancelledError: pass

def player_busy(uid):
    gkey = user_in_game.get(uid)
    if not gkey: return False
    if gkey in ttt_games: return True
    user_in_game.pop(uid, None); return False

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games()
        ua, cid, uid_a = u.effective_user, u.effective_chat.id, str(u.effective_user.id)
        name_a = (ua.first_name or "Player")[:20]
        vs_bot, user_b_id, name_b = True, None, "🤖 Bot"
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot, user_b_id, name_b = False, rb.id, (rb.first_name or "Player2")[:20]
                if player_busy(str(rb.id)):
                    await u.message.reply_text("⚠️ Player in game!"); return
        if player_busy(uid_a):
            await u.message.reply_text("⚠️ You're already in a game!"); return
        board = [TTT_EMPTY] * 9
        g = {"board": board, "turn": "X", "x_id": ua.id, "x_name": name_a, "o_id": user_b_id if not vs_bot else -1, "o_name": name_b, "vs_bot": vs_bot, "status": "playing", "created": time.time(), "chat_id": cid, "msg_id": None}
        msg = await u.message.reply_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        g["msg_id"] = msg.message_id
        gkey = game_key(msg.message_id, cid)
        ttt_games[gkey] = g
        game_timers[gkey] = {"remaining": 300}
        user_in_game[uid_a] = gkey
        if not vs_bot: user_in_game[str(user_b_id)] = gkey
        asyncio.create_task(run_game_timer(c, gkey))
    except Exception as e:
        logger.error(f"[tictac] {e}")

async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        try: await q.answer()
        except: pass
        parts = q.data.split(":")
        action, idx = parts[1], int(parts[2]) if len(parts) > 2 else -1
        cid, mid = q.message.chat_id, q.message.message_id
        gkey = game_key(mid, cid)
        g = ttt_games.get(gkey)
        if not g or g["status"] != "playing" or action == "noop": return
        uid = str(q.from_user.id)
        if g["turn"] == "X" and uid != str(g["x_id"]): return
        if g["turn"] == "O" and not g["vs_bot"] and uid != str(g["o_id"]): return
        board = g["board"]
        if idx < 0 or idx >= 9 or board[idx] != TTT_EMPTY: return
        if gkey in game_timers: game_timers[gkey]["remaining"] = 300
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        ws = ttt_check_winner(board)
        if ws:
            g["status"], g["winner_name"] = "win", (g["x_name"] if ws == TTT_X else g["o_name"])
            winner_uid = str(g["x_id"]) if ws == TTT_X else str(g["o_id"] if not g["vs_bot"] else -1)
            if winner_uid and winner_uid != "-1":
                await update_score(str(cid), winner_uid, g["winner_name"], +10)
            try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except: pass
            for uid in [str(g["x_id"]), str(g["o_id"])]: user_in_game.pop(uid, None)
            game_timers.pop(gkey, None); ttt_games.pop(gkey, None); return
        if ttt_is_draw(board):
            g["status"] = "draw"
            try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except: pass
            for uid in [str(g["x_id"]), str(g["o_id"])]: user_in_game.pop(uid, None)
            game_timers.pop(gkey, None); ttt_games.pop(gkey, None); return
        g["turn"] = "O" if g["turn"] == "X" else "X"
        if g["vs_bot"] and g["turn"] == "O":
            bi = ttt_bot_move(board)
            if bi >= 0:
                board[bi] = TTT_O
                ws2 = ttt_check_winner(board)
                if ws2 or ttt_is_draw(board):
                    g["status"] = "win" if ws2 else "draw"
                    if ws2: g["winner_name"] = (g["x_name"] if ws2 == TTT_X else g["o_name"])
                    try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
                    except: pass
                    user_in_game.pop(str(g["x_id"]), None)
                    game_timers.pop(gkey, None); ttt_games.pop(gkey, None); return
                g["turn"] = "X"
        try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        except: pass
    except Exception as e:
        logger.error(f"[ttt_cb] {e}")

# ═══════════════════════════════════════════════════════════════
# MINESWEEPER
# ═══════════════════════════════════════════════════════════════
def _mine_setup_keyboard(gkey):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("3 Mines", callback_data=f"mine:set:{gkey}:3"),
        InlineKeyboardButton("4 Mines", callback_data=f"mine:set:{gkey}:4"),
        InlineKeyboardButton("5 Mines", callback_data=f"mine:set:{gkey}:5")
    ]])

def _mine_board_keyboard(gkey, state, revealed, disabled=False):
    rows, r = [], []
    for i in range(6):
        if disabled or revealed[i]:
            label = "💣" if state[i] else ("✅" if revealed[i] else "⬜")
        else:
            label = "📦"
        btn = InlineKeyboardButton(label, callback_data=f"mine:play:{gkey}:{i}" if not disabled and not revealed[i] else f"mine:noop:{gkey}:{i}")
        r.append(btn)
        if len(r) == 3:
            rows.append(r); r = []
    if r: rows.append(r)
    return InlineKeyboardMarkup(rows)

def mine_build_text(g, rem):
    bombs, total_safe, opened = g["bombs"], 6 - g["bombs"], sum(1 for x in g["revealed"] if x)
    if g.get("status") == "timeout": return "⏰ *Time Up!*\n\nLost *-5 pts*."
    elif g.get("status") == "lost": return "💥 *BOOM!*\n\nLost *-5 pts*."
    elif g.get("status") == "won": return f"🎉 *YOU WIN!*\n\nAll {total_safe} safe boxes found! Won *+700 pts*."
    else: return f"💣 *MINESWEEPER*\nMines: {bombs}  |  Safe: {opened}/{total_safe}\n⏱ Time: `{rem}s`"

async def mine_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
        now = time.time()
        m_stat = mine_play_stats.setdefault(uid, {"plays": 0, "block_until": 0})
        if now < m_stat["block_until"]:
            rem_m = max(1, int((m_stat["block_until"] - now) // 60))
            await u.message.reply_text(f"⏳ *Cooldown!*\nWait {rem_m} minutes.", parse_mode=ParseMode.MARKDOWN); return
        m_stat["plays"] += 1
        if m_stat["plays"] > 20:
            m_stat["block_until"] = now + 3600; m_stat["plays"] = 0
            await u.message.reply_text("🛑 *Limit Hit!*\n1-hour break.", parse_mode=ParseMode.MARKDOWN); return
        gkey = f"{cid}_{uid}_{int(now)}"
        mine_games[gkey] = {"uid": uid, "name": (u.effective_user.first_name or "Player")[:20], "bombs": 0, "state": [], "revealed": [False]*6, "chat_id": u.effective_chat.id, "msg_id": None, "status": "setting"}
        msg = await u.message.reply_photo(photo=MINE_IMAGE_URL, caption="💣 *MINESWEEPER*\n\nChoose number of mines:", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_setup_keyboard(gkey))
        mine_games[gkey]["msg_id"] = msg.message_id
    except Exception as e:
        logger.error(f"[mine] {e}")

async def run_mine_timer(c, gkey):
    try:
        while True:
            await asyncio.sleep(5)
            g, td = mine_games.get(gkey), mine_timers.get(gkey)
            if not g or not td or g.get("status") != "playing": return
            td["remaining"] = max(0, td["remaining"] - 5)
            cid, msg_id = g.get("chat_id"), g.get("msg_id")
            if not msg_id: return
            if td["remaining"] <= 0:
                g["status"] = "timeout"
                new_sc = await update_score(str(cid), g["uid"], g["name"], -5)
                try:
                    await c.bot.edit_message_caption(chat_id=cid, message_id=msg_id, caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True))
                except: pass
                mine_timers.pop(gkey, None); mine_games.pop(gkey, None); return
            try:
                await c.bot.edit_message_caption(chat_id=cid, message_id=msg_id, caption=mine_build_text(g, td["remaining"]), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"]))
            except: pass
    except asyncio.CancelledError: pass

async def mine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        try: await q.answer()
        except: pass
        parts = q.data.split(":")
        action, gkey, val = parts[1], parts[2], int(parts[3])
        if gkey not in mine_games: return
        g = mine_games[gkey]
        if str(q.from_user.id) != g["uid"]:
            await q.answer("Not your game!"); return
        if action == "noop": return
        if action == "set":
            if g.get("status") != "setting": return
            bombs = max(3, min(5, val))
            state = [True]*bombs + [False]*(6-bombs)
            random.shuffle(state)
            g.update({"bombs": bombs, "state": state, "status": "playing", "revealed": [False]*6})
            mine_timers[gkey] = {"remaining": 60}
            asyncio.create_task(run_mine_timer(context, gkey))
            try: await q.edit_message_caption(caption=mine_build_text(g, 60), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, state, g["revealed"]))
            except: pass
        elif action == "play":
            if g.get("status") != "playing" or g["revealed"][val]: return
            is_bomb = g["state"][val]
            cid = str(q.message.chat_id)
            if is_bomb:
                g["status"] = "lost"
                mine_timers.pop(gkey, None)
                new_sc = await update_score(cid, g["uid"], g["name"], -5)
                try: await q.edit_message_caption(caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True))
                except: pass
                mine_games.pop(gkey, None)
            else:
                g["revealed"][val] = True
                total_safe = 6 - g["bombs"]
                opened_count = sum(1 for x in g["revealed"] if x)
                if gkey in mine_timers: mine_timers[gkey]["remaining"] = 60
                if opened_count >= total_safe:
                    g["status"] = "won"
                    mine_timers.pop(gkey, None)
                    new_sc = await update_score(cid, g["uid"], g["name"], +700)
                    try: await q.edit_message_caption(caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True))
                    except: pass
                    mine_games.pop(gkey, None)
                else:
                    rem = mine_timers.get(gkey, {}).get("remaining", 60)
                    try: await q.edit_message_caption(caption=mine_build_text(g, rem), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"]))
                    except: pass
    except Exception as e:
        logger.error(f"[mine_callback] {e}")

# ═══════════════════════════════════════════════════════════════
# SEARCH & BANANALOGIC
# ═══════════════════════════════════════════════════════════════
def wiki_summary(query):
    out = {"found": False, "title": "", "url": "", "intro": "", "sections": []}
    try:
        sr = requests.get("https://en.wikipedia.org/w/api.php", params={"action":"query","list":"search","srsearch":query,"srlimit":5,"format":"json"}, headers=WIKI_UA, timeout=10)
        hits = sr.json().get("query",{}).get("search",[])
        if not hits: return out
        best = hits[0]["title"]
        er = requests.get("https://en.wikipedia.org/w/api.php", params={"action":"query","titles":best,"prop":"extracts|info","inprop":"url","explaintext":"true","exsectionformat":"wiki","format":"json"}, headers=WIKI_UA, timeout=15)
        for pid, page in er.json().get("query",{}).get("pages",{}).items():
            if pid == "-1": continue
            raw = page.get("extract","").strip()
            url = page.get("fullurl", f"https://en.wikipedia.org/wiki/{urllib.parse.quote(best.replace(' ','_'))}")
            if not raw: continue
            parts = re.split(r"\n(==+)\s*(.+?)\s*\1\n", raw)
            intro = parts[0].strip()
            sections = []
            for i in range(1, len(parts)-2, 3):
                st = parts[i+1].strip() if i+1 < len(parts) else ""
                sb = parts[i+2].strip() if i+2 < len(parts) else ""
                if sb and st not in ("See also","References","Further reading","External links"): sections.append({"h": st, "b": sb[:800]})
            out.update({"found":True,"title":best,"url":url,"intro":intro[:1200],"sections":sections[:8]})
            break
    except: pass
    return out

def google_search(query):
    out = {"found": False, "ai_answer": "", "snippets": []}
    try:
        r = requests.get(f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=8&hl=en", headers=G_HDR, timeout=10)
        if r.status_code != 200: return out
        html = r.text
        for pat in [r'data-attrid="wa:/description"[^>]*>[\s\S]{0,200}?<span[^>]*>([^<]{40,800})', r'<div class="BNeawe s3v9rd AP7Wnd">([\s\S]{40,800}?)</div>']:
            m = re.search(pat, html, re.DOTALL)
            if m: out["ai_answer"] = clean_html(m.group(1))[:800]; break
        seen = set()
        for m in re.finditer(r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,350}?)</div', html, re.DOTALL):
            t = clean_html(m.group(1))
            if len(t) > 40 and t not in seen: seen.add(t); out["snippets"].append(t[:300])
            if len(out["snippets"]) >= 5: break
        out["found"] = bool(out["ai_answer"] or out["snippets"])
    except: pass
    return out

async def web_summarise(query, wiki, goog, system_prompt, max_tok=500):
    ctx = []
    if goog["ai_answer"]: ctx.append(f"Google Featured Answer: {goog['ai_answer']}")
    if goog["snippets"]: ctx.append("Web snippets:\n" + "\n".join(f"- {s}" for s in goog["snippets"]))
    if wiki["found"]: ctx.append(f"Wikipedia ({wiki['title']}):\n{wiki['intro']}")
    if not ctx: return ""
    return await ai(system_prompt, f"User question: {query}\n\nSearch facts:\n{chr(10).join(ctx)[:3000]}\n\nAnswer concisely.", "", max_tok=max_tok)

async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await u.message.reply_text("🐱 Usage: `/search query`"); return
    query = parts[1].strip()
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "🔍")
    sm = await u.message.reply_text("🔎 *Searching...*", parse_mode=ParseMode.MARKDOWN)
    loop = asyncio.get_running_loop()
    wiki, goog = await asyncio.gather(loop.run_in_executor(None, wiki_summary, query), loop.run_in_executor(None, google_search, query))
    summary = await web_summarise(query, wiki, goog, "Smart assistant. Write a clean concise summary in English. Max 250 words.")
    if summary:
        await sm.delete()
        await u.message.reply_text(f"🔍 *{query}*\n\n{summary}", parse_mode=ParseMode.MARKDOWN, reply_to_message_id=u.message.message_id)
    else:
        await sm.edit_text("😿 No results found.")

async def bananalogic_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await u.message.reply_text("🍌 Usage: `/bananalogic query`"); return
    query = parts[1].strip()
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "🍌")
    sm = await u.message.reply_text("🍌 *BananaLogic searching...*", parse_mode=ParseMode.MARKDOWN)
    loop = asyncio.get_running_loop()
    wiki, goog = await asyncio.gather(loop.run_in_executor(None, wiki_summary, query), loop.run_in_executor(None, google_search, query))
    answer = await web_summarise(query, wiki, goog, BANANA_PROMPT, max_tok=600)
    if answer:
        await sm.delete()
        # Quote style: user question in bold, then answer as reply
        text = f"❝ *{query}* ❞\n\n{answer}\n\n🐾 _via BananaLogic_"
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_to_message_id=u.message.message_id)
    else:
        await sm.edit_text("🍌 No response. Try again!")

# ═══════════════════════════════════════════════════════════════
# GENERAL CHAT - 2-LINE REPLIES, NO NLP, USE USER NAME
# ═══════════════════════════════════════════════════════════════
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot:
        return
    try:
        uid, cid, now = u.effective_user.id, str(u.effective_chat.id), datetime.now()

        # Anti-spam
        spam_tracker.setdefault(uid, [])
        spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
        spam_tracker[uid].append(now)
        if len(spam_tracker[uid]) >= 4:
            try: await u.message.delete()
            except: pass
            return

        # Track users
        db.setdefault("seen",{}).setdefault(cid,{})[str(uid)] = {
            "id": uid, "un": u.effective_user.username, "n": u.effective_user.first_name or "User"
        }
        counts = db.setdefault("counts", {})
        counts[cid] = counts.get(cid, 0) + 1

        # Sentiment reaction every 6th msg
        if counts[cid] % 6 == 0:
            text_for_react = (u.message.text or u.message.caption or "").strip()
            _, emoji = analyze_sentiment(text_for_react)
            try: await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
            except: pass

        text = (u.message.text or u.message.caption or "").strip()
        if text.startswith("/"): return

        bot_username = bot_status.get("username", "")
        text_low = text.lower()
        contains_beluga = "beluga" in text_low
        contains_username = bool(bot_username) and (bot_username in text_low or f"@{bot_username}" in text_low)
        is_reply = (u.message.reply_to_message and u.message.reply_to_message.from_user
                    and u.message.reply_to_message.from_user.id == c.bot.id)

        # Image analysis
        if u.message.reply_to_message and (u.message.reply_to_message.photo or u.message.reply_to_message.sticker):
            await analyze_image_with_cv(u, c)
            return

        # AI Chat
        if text and (contains_beluga or contains_username or is_reply):
            try: await asyncio.wait_for(c.bot.send_chat_action(u.effective_chat.id, "typing"), timeout=4.0)
            except: pass

            emoji = await ai_emoji(text)
            try: await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
            except: pass

            user_name = get_user_name(u.effective_user)
            system = (f"{CHAT_PROMPT}\n"
                      f"The user's name is {user_name}. Always address them by name in your reply.\n"
                      f"Reply in EXACTLY 2 lines. No NLP tags. No analysis. Just natural friendly reply.")

            reply = await ai(system, text, f"Meow {user_name}! 🐾", max_tok=120)

            # Save memory
            if text and len(text) > 5:
                await save_chat_memory(cid, str(uid), user_name, text)

            try:
                await u.message.reply_text(reply, reply_to_message_id=u.message.message_id)
            except:
                pass

        bot_status["message_count"] += 1
        bot_status["last_update"] = datetime.now()
    except Exception as e:
        logger.error(f"[monitor] {e}")

# ═══════════════════════════════════════════════════════════════
# HTTP HEALTH
# ═══════════════════════════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({"status": "healthy", "uptime_seconds": up, "running": bot_status["running"], "messages": bot_status["message_count"], "version": "11.1.0"})

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()})

async def start_http(port):
    aio = web.Application()
    aio.router.add_get("/", _ping)
    aio.router.add_get("/health", _health)
    aio.router.add_get("/ping", _ping)
    runner = web.AppRunner(aio)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ HTTP @ 0.0.0.0:{port}")
    return runner

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut, Forbidden)): return
    if isinstance(err, RetryAfter):
        await asyncio.sleep(err.retry_after + 1); return
    if isinstance(err, BadRequest) and "not modified" in str(err).lower(): return
    bot_status["error_count"] += 1
    logger.error(f"[Err] {err}")
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ *Error:* `{str(err)[:150]}`", parse_mode=ParseMode.MARKDOWN)
        except: pass

# ═══════════════════════════════════════════════════════════════
# START COMMAND - QUOTED CLASSY DESIGN + UPDATES CHANNEL BUTTON
# ═══════════════════════════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return

    user_name = get_user_name(u.effective_user) if u.effective_user else "there"

    text = (
        f"*Hey {user_name}!* 👋\n\n"
        "┌─────────────────────────────┐\n"
        "│   ✨ *BELUGA BOT v11.1.0* ✨   │\n"
        "│  🐱 _Your AI Crypto Companion_  │\n"
        "│       *from* @BELUGAPY         │\n"
        "└─────────────────────────────┘\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎮 *GAMES*\n"
        "┣ `/quiz` `[topic]` — Brain Trivia\n"
        "┣ `/tictac` `[@user]` — Tic Tac Toe\n"
        "┣ `/mine` — 💣 Minesweeper\n"
        "┗ `/gay` `/couple` — Daily Fun\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💰 *CRYPTO LIVE*\n"
        "┣ `/price` `BTC` — Live Price\n"
        "┣ `/topgainers` — 📈 Top Gainers\n"
        "┣ `/toplosers` — 📉 Top Losers\n"
        "┗ `/chart` `BTC 1h` — Candlestick\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📰 *NEWS*\n"
        "┣ `/news` — Crypto Headlines\n"
        "┣ `/ainews` — AI & ML Updates\n"
        "┗ `/technews` — Tech World\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 *SEARCH & AI*\n"
        "┣ `/search` `query` — Web Search\n"
        "┣ `/bananalogic` `query` — AI Answer\n"
        "┗ _@ mention me to chat with Beluga!_\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🖼️ *IMAGE TOOLS*\n"
        "┣ `/qr` `text` — QR Generator\n"
        "┣ `/scanqr` — Scan QR Code\n"
        "┣ `/resize` — Resize to 512×512\n"
        "┣ `/compress` — Compress Image\n"
        "┣ `/watermark` `text` — Add Watermark\n"
        "┗ `/imginfo` — Image Details\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏆 *LEADERBOARD*\n"
        "┣ `/lb` — View Rankings\n"
        "┣ `/gm` — Morning Check-in *(admin)*\n"
        "┣ `/nw` — New Week Reset *(admin)*\n"
        "┗ `/pump` `/dump` — Edit Points *(admin)*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *TIP:* Reply to any image and ask\n"
        "_\"What's in this?\"_ and Beluga will analyze it! 👁️\n\n"
        "❝ _Built with 💙 by @BELUGAPY_ ❞"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 UPDATES CHANNEL", url=UPDATES_CHANNEL)
    ]])

    await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
async def main():
    logger.info("🐱 STARTING BELUGA BOT v11.1.0")
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    app = TGApp.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("price", crypto_price_handler))
    app.add_handler(CommandHandler(["topgainers","toplosers"], crypto_movers_handler))
    app.add_handler(CommandHandler(["chart","chart5m","chart15m","chart1h","chart4h","chart1d"], crypto_chart_handler))
    app.add_handler(CommandHandler("news", lambda u, c: execute_news_flow(u, c, "crypto", "Crypto News")))
    app.add_handler(CommandHandler("ainews", lambda u, c: execute_news_flow(u, c, "ai", "AI News")))
    app.add_handler(CommandHandler("technews", lambda u, c: execute_news_flow(u, c, "tech", "Tech News")))
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("bananalogic", bananalogic_handler))
    app.add_handler(CommandHandler("qr", qr_generate_handler))
    app.add_handler(CommandHandler("scanqr", qr_scan_handler))
    app.add_handler(CommandHandler("resize", lambda u, c: img_handler(u, c, "resize")))
    app.add_handler(CommandHandler("compress", lambda u, c: img_handler(u, c, "compress")))
    app.add_handler(CommandHandler("watermark", watermark_handler))
    app.add_handler(CommandHandler("imginfo", lambda u, c: img_handler(u, c, "info")))
    app.add_handler(CommandHandler("quiz", quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler("nw", nw_handler))
    app.add_handler(CommandHandler(["pump","dump"], pump_dump_handler))
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("mine", mine_handler))
    app.add_handler(CommandHandler("gm", gm_handler))
    app.add_handler(CommandHandler(["gay","couple"], fun_dispatcher))

    # Callbacks
    app.add_handler(CallbackQueryHandler(ttt_callback, pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(gm_callback, pattern=r"^gm:"))
    app.add_handler(CallbackQueryHandler(mine_callback, pattern=r"^mine:"))
    app.add_handler(CallbackQueryHandler(watermark_callback, pattern=r"^wm:"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    # Chat monitor (text only)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)

    await app.initialize()
    await app.start()

    try:
        me = await app.bot.get_me()
        bot_status["username"] = me.username.lower()
        logger.info(f"🤖 Bot: @{me.username}")
    except Exception as e:
        logger.warning(f"[Startup] {e}")

    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=[])
    bot_status["running"] = True

    stop_evt = asyncio.Event()
    try:
        import signal
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT, stop_evt.set)
    except: pass

    cleanup_task = asyncio.create_task(cleanup_expired_games())
    sync_task = asyncio.create_task(periodic_github_sync())

    await stop_evt.wait()
    cleanup_task.cancel(); sync_task.cancel()
    bot_status["running"] = False
    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try: await fn()
        except: pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        sys.exit(1)
