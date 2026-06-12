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

fun_cache = {"gay": {}, "couple": {}}
fun_cache_lock = asyncio.Lock()

exchange_cache = {"binance": None, "bybit": None, "okx": None, "bitget": None, "kraken": None}
cache_ticker = {}
cache_movers = {"ts": 0, "data": {}}
news_cache = {"crypto": {"ts": 0, "data": []}, "ai": {"ts": 0, "data": []}, "tech": {"ts": 0, "data": []}}

LB_IMAGE_URL = "https://i.postimg.cc/P5THW6RQ/file-00000000bce4720b905dc2e04c58fa80.png"
MINE_IMAGE_URL = "https://i.postimg.cc/hjCftW5b/file-0000000079a071fa95971d3b70015fc0.png"
GM_IMAGE_URL = "https://i.postimg.cc/Fs1h0CPs/file-000000001d7872078a894cdf6f6247c9.png"

CHAT_PROMPT = """You are Beluga, a cute female cat from Team Oldy Crypto. Stay in character.
Personality: warm, playful, intelligent, helpful. Keep responses 1-3 sentences unless asked for more.
Understand mood and respond empathetically.
Be naturally flirty when appropriate. Use light cat expressions (🐾, meow, purr) but don't overuse."""

BANANA_PROMPT = """You are Beluga answering using web search results. Be concise, accurate, conversational. Answer in user's language.
Summarize relevant facts and directly answer from provided data. Don't say you searched. Just answer naturally as Beluga would."""

QUIZ_TOPICS = ["deep ocean biology","quantum mechanics","human brain","solar system","animal behaviour","black holes","DNA genetics","ancient Egypt","World War 2"]
FALLBACK_QS = [
    {"q":"Which planet has most moons?","opts":["Jupiter","Saturn","Uranus","Neptune"],"ans":1,"fact":"Saturn: 146 moons!"},
    {"q":"What covers 71% of Earth?","opts":["Land","Ice","Water","Air"],"ans":2,"fact":"Oceans!"},
]
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
WIKI_UA = {"User-Agent": "BelugaBot/11.0"}
G_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "en-US,en;q=0.9"}

# Sentiment emojis
SENTIMENT_POSITIVE = ["😊", "😄", "❤️", "🔥", "✨", "🎉", "💖", "😻", "👍"]
SENTIMENT_NEGATIVE = ["😢", "😠", "💔", "😤", "😭", "😞", "😿", "😡", "⚠️"]
SENTIMENT_NEUTRAL = ["🤔", "😐", "👀", "🐾", "🎯", "📌", "💭", "🤷"]

def get_exchange(prefer: str = "bybit"):
    """Get working exchange instance with fallback chain"""
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
    """Load group data from GitHub"""
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
    """Load fun DB from GitHub"""
    global fun_db, fun_db_loaded
    if fun_db_loaded:
        return
    fun_db_loaded = True
    loop = asyncio.get_running_loop()
    d = await loop.run_in_executor(None, gh_rw, "read", "beluga_fun.json", fun_db)
    if d:
        fun_db = d

async def periodic_github_sync():
    """Sync to GitHub every 30 seconds"""
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
    """Update user score in memory + mark for sync"""
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

# ═══════════════════════════════════════════════════════════════
# SENTIMENT ANALYSIS & EMOJI SELECTION
# ═══════════════════════════════════════════════════════════════
def analyze_sentiment(text: str) -> tuple[float, str]:
    """Analyze sentiment and return score + emoji"""
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
# ADVANCED CV IMAGE ANALYSIS (Object Detection)
# ═══════════════════════════════════════════════════════════════
def advanced_image_analysis(img) -> str:
    """Advanced CV analysis - detect objects, text, people"""
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Edge detection
        edges = cv2.Canny(gray, 50, 150)
        
        # Contour detection (objects)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Color analysis
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        colors = {}
        
        # Detect major colors
        hist = cv2.calcHist([hsv], [0], None, [180], [0, 180])
        dominant_hue = np.argmax(hist)
        
        hue_names = {
            (0, 10): "Red", (10, 25): "Orange", (25, 35): "Yellow",
            (35, 60): "Green", (60, 90): "Cyan", (90, 120): "Blue",
            (120, 140): "Purple", (140, 170): "Pink"
        }
        
        color_detected = "Multiple colors"
        for (hmin, hmax), name in hue_names.items():
            if hmin <= dominant_hue <= hmax:
                color_detected = name
                break
        
        # Text detection (using contour complexity)
        text_complexity = 0
        for contour in contours:
            approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
            if len(approx) > 5:
                text_complexity += 1
        
        # Build analysis
        analysis = f"🖼️ *Image Analysis*\n\n"
        analysis += f"📐 Size: {w}×{h}px\n"
        analysis += f"🎨 Dominant Color: {color_detected}\n"
        analysis += f"📦 Objects/Shapes: {len(contours)}\n"
        
        if len(contours) > 20:
            analysis += "💭 Contains: Complex scene with many elements\n"
        elif len(contours) > 10:
            analysis += "💭 Contains: Multiple distinct objects\n"
        elif len(contours) > 3:
            analysis += "💭 Contains: Several objects/elements\n"
        else:
            analysis += "💭 Contains: Simple/minimal composition\n"
        
        if text_complexity > 5:
            analysis += "📝 Text detected: Yes, contains writing\n"
        
        # Brightness analysis
        brightness = np.mean(gray)
        if brightness > 180:
            analysis += "☀️ Brightness: Very bright/light\n"
        elif brightness > 120:
            analysis += "💡 Brightness: Bright\n"
        elif brightness > 80:
            analysis += "⚪ Brightness: Normal\n"
        else:
            analysis += "🌙 Brightness: Dark/dim\n"
        
        analysis += "\n_Analyzed via Beluga Vision_"
        return analysis
    except Exception as e:
        return f"📸 Image: Unable to analyze. {str(e)[:30]}"

# ═══════════════════════════════════════════════════════════════
# AI & SENTIMENT
# ═══════════════════════════════════════════════════════════════
async def _groq_async(system: str, user: str, max_tok: int = 400) -> Optional[str]:
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

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 400) -> str:
    try:
        res = await asyncio.wait_for(_groq_async(system, user, max_tok), timeout=14)
        if res:
            return res
    except:
        pass
    return fallback

async def ai_emoji(text: str) -> str:
    try:
        res = await asyncio.wait_for(_groq_async("Output ONE emoji matching emotion. ONLY the emoji.", f"Text: '{text[:60]}'", 10), timeout=6)
        if res:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", res)
            if found:
                return found[0][0]
    except:
        pass
    return "😼"

def process_linguistic_sentiment_analysis(text: str) -> str:
    try:
        detected_lang = detect(text)
    except:
        detected_lang = "en"
    try:
        polarity = TextBlob(text).sentiment.polarity
    except:
        polarity = 0.0
    
    if polarity > 0.35:
        mood = " Be exceptionally cheerful, friendly, warm."
    elif polarity < -0.35:
        mood = " Be deeply empathetic, supportive, sweet."
    else:
        mood = ""
    
    return f"{CHAT_PROMPT}\n- Language: `{detected_lang}`.{mood}"

# ═══════════════════════════════════════════════════════════════
# CHAT MEMORY SYSTEM
# ═══════════════════════════════════════════════════════════════
async def save_chat_memory(cid: str, uid: str, name: str, message: str):
    """Save chat memory for context"""
    global fun_db_needs_sync
    await check_and_load_fun_db()
    
    memory_key = f"{cid}:{uid}"
    if memory_key not in fun_db.get("chat_memory", {}):
        fun_db["chat_memory"][memory_key] = []
    
    fun_db["chat_memory"][memory_key].append({
        "time": datetime.now().isoformat(),
        "msg": message[:100],
        "name": name
    })
    
    # Keep only last 5 messages per user
    if len(fun_db["chat_memory"][memory_key]) > 5:
        fun_db["chat_memory"][memory_key] = fun_db["chat_memory"][memory_key][-5:]
    
    fun_db_needs_sync = True

# ═══════════════════════════════════════════════════════════════
# CRYPTO COMMANDS (CCXT Only)
# ═══════════════════════════════════════════════════════════════
async def crypto_price_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Fetch crypto price from CCXT"""
    if not u.message or not exchange:
        return
    try:
        ticker = (c.args[0].upper() if c.args else "BTC")
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "💰")
        sm = await u.message.reply_text(f"⚡ *Market Price Retrieval*\n*Progress: Syncing {ticker}/USDT...*", parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        
        try:
            td = await loop.run_in_executor(None, exchange.fetch_ticker, f"{ticker}/USDT")
            price = td.get('last', 0.0)
            change = td.get('percentage', 0.0)
            vol = td.get('baseVolume', 0.0)
            high = td.get('high', 0.0)
            low = td.get('low', 0.0)
            
            sign = "🟩 +" if change >= 0 else "🟥 "
            
            res = f"⚡ *{ticker}/USDT*\n"
            res += "━━━━━━━━━━━━━━━━━━━━\n\n"
            res += f"🏷 *Price*\n`{price:,.4f} USDT`\n\n"
            res += f"📊 *24h Change*\n`{sign}{change:.2f}%`\n\n"
            res += f"📈 *24h High*\n`{high:,.4f}`\n\n"
            res += f"📉 *24h Low*\n`{low:,.4f}`\n\n"
            res += f"🔄 *Volume*\n`{vol:,.2f} {ticker}`\n\n"
            res += "━━━━━━━━━━━━━━━━━━━━\n"
            res += "🐾 _via Beluga Quant Engine_"
            
            await sm.edit_text(res, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await sm.edit_text(f"😿 Error: `{str(e)[:60]}`")
            bot_status["error_count"] += 1
    except Exception as e:
        logger.error(f"[crypto_price] {e}")

async def crypto_movers_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Top gainers/losers from CCXT with Telegram Quote"""
    if not u.message or not exchange:
        return
    try:
        gainers_mode = "topgainers" in u.message.text.lower()
        lbl = "Gainers" if gainers_mode else "Losers"
        sm = await u.message.reply_text(f"⚡ *Volatility Sort Matrix*\n*Progress: Finding top {lbl.lower()}...*", parse_mode=ParseMode.MARKDOWN)
        
        loop = asyncio.get_running_loop()
        
        # Check cache
        now = time.time()
        if cache_movers["ts"] and (now - cache_movers["ts"]) < 60:
            tickers = cache_movers["data"]
        else:
            tickers = await loop.run_in_executor(None, exchange.fetch_tickers)
            cache_movers["ts"] = now
            cache_movers["data"] = tickers
        
        records = [
            {"sym": sym.split("/")[0], "ch": t.get('percentage', 0.0), "price": t.get('last', 0.0)}
            for sym, t in tickers.items() if sym.endswith("/USDT")
        ]
        records.sort(key=lambda x: x["ch"], reverse=gainers_mode)
        
        text = f"📊 *TOP 5 {lbl.upper()} (24H)*\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, r in enumerate(records[:5], 1):
            s = "🟩 +" if r["ch"] >= 0 else "🟥 "
            text += f"*{i}. {r['sym']}*\n"
            text += f"Price: `{r['price']:,.3f}` USDT\n"
            text += f"Change: `{s}{r['ch']:.2f}%`\n\n"
        
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += "🐾 _Data updated_"
        
        await sm.edit_text(text, parse_mode=ParseMode.MARKDOWN, 
                          reply_to_message_id=u.message.message_id)
    except Exception as e:
        logger.error(f"[crypto_movers] {e}")
        try:
            await sm.edit_text(f"😿 Error: `{str(e)[:50]}`")
        except:
            pass

async def crypto_chart_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Generate candlestick chart via CCXT"""
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
        sm = await u.message.reply_text(f"📊 *Chart Visualization*\n*Progress: Fetching {ticker} ({timeframe})...*", parse_mode=ParseMode.MARKDOWN)
        
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
# NEWS COMMANDS (Google News RSS + No Repeat)
# ═══════════════════════════════════════════════════════════════
def fetch_google_news(feed_type: str) -> list[dict]:
    """Fetch from Google News RSS"""
    feeds = {
        "crypto": "https://news.google.com/rss/search?q=cryptocurrency",
        "ai": "https://news.google.com/rss/search?q=artificial+intelligence",
        "tech": "https://news.google.com/rss/search?q=technology"
    }
    url = feeds.get(feed_type, feeds["tech"])
    results = []
    
    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:15]:  # Fetch more to avoid repeats
            title = entry.get("title", "No Title")
            link = entry.get("link", "#")
            pub_date = entry.get("published", "")
            
            img_url = None
            summary_html = entry.get("summary", "")
            
            img_match = re.search(r'src=["\'](https://[^"\']+\.(?:jpg|jpeg|png|webp|gif))["\']', summary_html, re.IGNORECASE)
            if img_match:
                img_url = img_match.group(1)
            
            summary_text = BeautifulSoup(summary_html, "html.parser").get_text()[:160]
            
            results.append({
                "title": title[:200],
                "link": link,
                "summary": summary_text,
                "image": img_url,
                "date": pub_date[:16]
            })
    except Exception as e:
        logger.error(f"[fetch_google_news] {e}")
    
    return results

async def execute_news_flow(u: Update, c: ContextTypes.DEFAULT_TYPE, feed_type: str, label: str):
    """Execute news command with rotation"""
    if not u.message:
        return
    
    try:
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "📰")
        sm = await u.message.reply_text(f"🛰 *News Feed*\n*Progress: Fetching {label}...*", parse_mode=ParseMode.MARKDOWN)
        
        loop = asyncio.get_running_loop()
        
        # Check cache and rotate through news
        now = time.time()
        if news_cache[feed_type]["ts"] and (now - news_cache[feed_type]["ts"]) < 300:  # 5 min cache
            items = news_cache[feed_type]["data"]
        else:
            items = await loop.run_in_executor(None, fetch_google_news, feed_type)
            news_cache[feed_type]["ts"] = now
            news_cache[feed_type]["data"] = items
        
        if not items:
            await sm.edit_text("😿 No news found.")
            return
        
        await sm.delete()
        
        # Rotate through different news articles
        idx = random.randint(0, min(len(items) - 1, 4))
        top = items[idx]
        
        if top["image"]:
            try:
                cap = f"📰 *{label}*\n\n*{top['title'][:150]}*\n\n{top['summary'][:120]}...\n\n📅 {top['date']}\n\n🔗 [Read More]({top['link']})"
                await u.message.reply_photo(photo=top["image"], caption=cap, parse_mode=ParseMode.MARKDOWN,
                                           reply_to_message_id=u.message.message_id)
            except:
                cap = f"📰 *{label}*\n\n*{top['title'][:150]}*\n\n{top['summary'][:120]}...\n\n📅 {top['date']}\n\n🔗 [Read More]({top['link']})"
                await u.message.reply_text(cap, parse_mode=ParseMode.MARKDOWN,
                                         reply_to_message_id=u.message.message_id)
        else:
            cap = f"📰 *{label}*\n\n*{top['title'][:150]}*\n\n{top['summary'][:120]}...\n\n📅 {top['date']}\n\n🔗 [Read More]({top['link']})"
            await u.message.reply_text(cap, parse_mode=ParseMode.MARKDOWN,
                                     reply_to_message_id=u.message.message_id)
        
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[execute_news_flow] {e}")

# ═══════════════════════════════════════════════════════════════
# QR & IMAGE TOOLS
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
        sm = await u.message.reply_text("🟩 *Matrix Transformation*\n*Progress: Translating to QR...*", parse_mode=ParseMode.MARKDOWN)
        
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
        sm = await u.message.reply_text("🟩 *Computer Vision Scan*\n*Progress: Decoding...*", parse_mode=ParseMode.MARKDOWN)
        
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
            await sm.edit_text(f"🤖 *Decoded Payload:*\n```\n{decoded_text}\n```", parse_mode=ParseMode.MARKDOWN)
        else:
            await sm.edit_text("😿 QR data unreadable.")
    except Exception as e:
        logger.error(f"[qr_scan] {e}")

async def img_handler(u: Update, c: ContextTypes.DEFAULT_TYPE, action: str):
    """Image tools: compress, resize, watermark, info"""
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Reply to a photo.")
        return
    try:
        sm = await u.message.reply_text(f"📦 *Image {action.title()}*\n*Progress: Processing...*", parse_mode=ParseMode.MARKDOWN)
        
        p = u.message.reply_to_message.photo[-1]
        f = await c.bot.get_file(p.file_id)
        b = io.BytesIO()
        await f.download_to_memory(b)
        b.seek(0)
        
        loop = asyncio.get_running_loop()
        
        if action == "info":
            im = Image.open(b)
            await sm.edit_text(
                f"🖼 *Image Report*\n━━━━━━━━━━━━━━━━━━━━\n📐 *Resolution:* `{im.size[0]} x {im.size[1]} pixels`\n🎨 *Color Mode:* `{im.mode}`\n💾 *Size:* `{p.file_size / 1024:.2f} KB`\n🧱 *Format:* `{im.format}`\n━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.MARKDOWN
            )
        elif action == "resize":
            def _scale():
                im = Image.open(b)
                out = im.resize((512, 512), Image.Resampling.LANCZOS)
                out_b = io.BytesIO()
                out.save(out_b, "PNG")
                out_b.seek(0)
                return out_b
            
            res_b = await loop.run_in_executor(None, _scale)
            await sm.delete()
            await u.message.reply_photo(photo=res_b, caption="📐 *Resized to 512x512.*")
        elif action == "compress":
            def _crunch():
                im = Image.open(b)
                out_b = io.BytesIO()
                im.save(out_b, "JPEG", quality=22)
                out_b.seek(0)
                return out_b
            
            res_b = await loop.run_in_executor(None, _crunch)
            await sm.delete()
            await u.message.reply_photo(photo=res_b, caption="💾 *Compressed.*")
        elif action == "watermark":
            wm_text = u.message.text.split(maxsplit=1)[1].strip() if len(u.message.text.split(maxsplit=1)) > 1 else "TEAM OLDY CRYPTO"
            
            def _inject():
                im = Image.open(b).convert("RGBA")
                txt_layer = Image.new("RGBA", im.size, (255, 255, 255, 0))
                draw = ImageDraw.Draw(txt_layer)
                x, y = im.size[0] // 2 - 100, im.size[1] - 50
                draw.text((x, y), wm_text, fill=(255, 196, 140, 160))
                combined = Image.alpha_composite(im, txt_layer)
                out_b = io.BytesIO()
                combined.convert("RGB").save(out_b, "JPEG")
                out_b.seek(0)
                return out_b
            
            res_b = await loop.run_in_executor(None, _inject)
            await sm.delete()
            await u.message.reply_photo(photo=res_b, caption="🛡 *Watermark Applied.*")
    except Exception as e:
        logger.error(f"[img_handler] {e}")

# ═══════════════════════════════════════════════════════════════
# COMPUTER VISION - Advanced Image Analysis
# ═══════════════════════════════════════════════════════════════
async def analyze_image_with_cv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Advanced CV-based image analysis when user asks about image"""
    if not update.message:
        return
    
    if not update.message.reply_to_message:
        return
    
    replied = update.message.reply_to_message
    has_media = bool(replied.photo or replied.video or replied.sticker or replied.document)
    
    if not has_media:
        return
    
    text = (update.message.text or "").lower()
    question_words = ["what", "this", "image", "photo", "video", "sticker", "show", "see", "contain", "display", "in", "who", "describe", "tell"]
    is_question = any(word in text for word in question_words)
    
    if not is_question or len(text) < 3:
        return
    
    try:
        sm = await update.message.reply_text("👀 *Analyzing image...*", parse_mode=ParseMode.MARKDOWN)
        
        if replied.photo:
            file_obj = await context.bot.get_file(replied.photo[-1].file_id)
        elif replied.sticker:
            file_obj = await context.bot.get_file(replied.sticker.file_id)
        elif replied.document and replied.document.mime_type and replied.document.mime_type.startswith("image"):
            file_obj = await context.bot.get_file(replied.document.file_id)
        elif replied.video:
            file_obj = await context.bot.get_file(replied.video.file_id)
        else:
            await sm.edit_text("😿 Unsupported media type.")
            return
        
        buf = io.BytesIO()
        await file_obj.download_to_memory(buf)
        buf.seek(0)
        
        loop = asyncio.get_running_loop()
        
        def _analyze():
            try:
                arr = np.frombuffer(buf.getvalue(), dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    return "Cannot decode image"
                return advanced_image_analysis(img)
            except Exception as e:
                return f"Error: {str(e)[:50]}"
        
        result = await loop.run_in_executor(None, _analyze)
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
            raw = await ai("Trivia master. Output ONLY raw JSON.", f"Topic: '{topic}'. Generate 1 MC question.\n" '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}', "", max_tok=200)
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
        await c.bot.send_chat_action(cid_i, "typing")
        sm = await u.message.reply_text("🎲 *Quiz Generation*\n*Progress: Building prompts...*", parse_mode=ParseMode.MARKDOWN)
        qdata = await gen_quiz(topic, cid)
        try:
            await sm.delete()
        except:
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
                active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":qdata["correct_index"]}
                bot_status["message_count"] += 1
                return
            except:
                pass
        
        fb = random.choice(FALLBACK_QS)
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
        active_polls[pm.poll.id] = {"chat_id":cid_i,"correct_index":fb["ans"]}
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[quiz] {e}")
        bot_status["error_count"] += 1

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
# LEADERBOARD & SCORING
# ═══════════════════════════════════════════════════════════════
async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Display leaderboard from GitHub data"""
    if not u.message:
        return
    try:
        cid = str(u.effective_chat.id)
        await check_and_load_group(cid)
        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)
        
        seen_ids = set()
        clean_lb = [e for e in lb if e.get("user_id") not in seen_ids and not seen_ids.add(e.get("user_id"))]
        
        lw = db.get("weekly", {}).get(cid, {})
        lines = []
        
        if lw and lw.get("top3"):
            lines.append("🏆 LAST WEEK CHAMPIONS 🏆\n")
            lines.extend([f"{MEDALS[i]} {e.get('name','?')[:18]} — {e.get('score',0):,} pts" for i, e in enumerate(lw["top3"])])
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n")
        
        lines += ["╔════════════════════════════╗", "🏆  CURRENT LEADERBOARD  🏆", "╚════════════════════════════╝\n"]
        
        if not clean_lb:
            lines.append("No scores yet!")
        else:
            for i, e in enumerate(clean_lb[:10]):
                m = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
                lines.append(f"{m} {e.get('name','Unknown')[:18]:<18} {e.get('score',0):>6,} pts")
        
        lines += ["\n━━━━━━━━━━━━━━━━━━━━", "➕ +10 quiz/ttt · +700 mine · +50 gm"]
        text = "\n".join(lines)
        
        try:
            await u.message.reply_photo(photo=LB_IMAGE_URL, caption=text, parse_mode=ParseMode.MARKDOWN)
        except:
            await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[lb] {e}")

async def nw_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """New week - reset scores, save top 3"""
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
    """Admin pump/dump points"""
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
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[pump_dump] {e}")

# ═══════════════════════════════════════════════════════════════
# FUN COMMANDS - /gay /couple (24h persistent, all members)
# ═══════════════════════════════════════════════════════════════
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Gay/Couple daily random selection from ALL group members (24h persistence)"""
    global fun_db_needs_sync
    
    if not u.message:
        return
    
    try:
        cid = str(u.effective_chat.id)
        await check_and_load_group(cid)
        await check_and_load_fun_db()
        
        cmd = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        
        # Get all group members (using 'seen' tracking from all messages)
        # If not enough in seen, try to get from cache
        active_users = list(db.get("seen", {}).get(cid, {}).values())
        
        # If we don't have enough users, fetch from chat (slow but comprehensive)
        if len(active_users) < (2 if cmd == "couple" else 1):
            # Fallback: use cached admin/owner
            if OWNER_ID:
                active_users.append({"id": OWNER_ID, "un": "Owner", "n": "Owner"})
        
        if len(active_users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text("😿 Need more group members!")
            return
        
        day = datetime.now().strftime("%y-%m-%d")
        lk = f"{cid}:{cmd}:{day}"
        
        async with fun_cache_lock:
            # Check cache (24h)
            if lk in fun_db["gay_couple_log"]:
                cached = fun_db["gay_couple_log"][lk]
                cached_day = cached.get("date", "")
                if cached_day == day:
                    await u.message.reply_text(cached["result"], parse_mode=ParseMode.MARKDOWN)
                    bot_status["message_count"] += 1
                    return
        
        # Generate new result (from ALL members)
        if cmd == "couple":
            m = random.sample(active_users, min(2, len(active_users)))
            if len(m) == 2:
                res = f"💖 *{m[0]['n']}* 💞 *{m[1]['n']}*\n100% compatible!"
            else:
                res = f"💖 *{m[0]['n']}* needs a partner! 💔"
        else:  # gay
            m = [random.choice(active_users)]
            res = f"🌈 *{m[0]['n']}* is today's rainbow! 🌈"
        
        # Save to fun DB
        async with fun_cache_lock:
            fun_db["gay_couple_log"][lk] = {
                "date": day,
                "result": res,
                "users": [p.get("id") for p in m]
            }
            fun_db_needs_sync = True
        
        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[fun_dispatcher] {e}")

# ═══════════════════════════════════════════════════════════════
# GOOD MORNING & ATTENDANCE
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
            bot_status["message_count"] += 1
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
                        chat_id=q.message.chat_id,
                        message_id=msg_id,
                        caption=new_cap,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]])
                    )
                else:
                    await context.bot.edit_message_text(
                        chat_id=q.message.chat_id,
                        message_id=msg_id,
                        text=new_cap,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]])
                    )
                await q.answer(f"✅ +50 pts")
            except:
                await q.answer("✅ Marked!")
    except Exception as e:
        logger.error(f"[gm_callback] {e}")

# ═══════════════════════════════════════════════════════════════
# TIC TAC TOE (Minimal Implementation)
# ═══════════════════════════════════════════════════════════════
TTT_EMPTY, TTT_X, TTT_O = "⬜", "❌", "⭕"
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
    if w == TTT_O:
        return 10
    if w == TTT_X:
        return -10
    if all(c != TTT_EMPTY for c in board):
        return 0
    best = -1000 if is_max else 1000
    for i in range(9):
        if board[i] != TTT_EMPTY:
            continue
        board[i] = TTT_O if is_max else TTT_X
        score = _minimax(board, not is_max, alpha, beta)
        board[i] = TTT_EMPTY
        if is_max:
            best = max(best, score)
            alpha = max(alpha, best)
        else:
            best = min(best, score)
            beta = min(beta, best)
        if beta <= alpha:
            break
    return best

def ttt_bot_move(board: list) -> int:
    best_score, best_move = -1000, -1
    for i in range(9):
        if board[i] != TTT_EMPTY:
            continue
        board[i] = TTT_O
        score = _minimax(board, False, -1000, 1000)
        board[i] = TTT_EMPTY
        if score > best_score:
            best_score, best_move = score, i
    return best_move

def ttt_build_keyboard(board: list, disabled: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx = row*3 + col
            cb = f"ttt:noop:{idx}" if (board[idx] != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            r.append(InlineKeyboardButton(board[idx], callback_data=cb))
        rows.append(r)
    return InlineKeyboardMarkup(rows)

def ttt_build_text(g: dict) -> str:
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

async def run_game_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    try:
        while True:
            await asyncio.sleep(5)
            g = ttt_games.get(gkey)
            td = game_timers.get(gkey)
            if not g or not td or g.get("status") != "playing":
                return
            
            td["remaining"] = max(0, td["remaining"] - 5)
            cid, msg_id = g.get("chat_id"), g.get("msg_id")
            
            if not msg_id:
                return
            
            if td["remaining"] <= 0:
                g["status"] = "timeout"
                g["winner_name"] = (g["o_name"] if g["turn"] == "X" else g["x_name"])
                try:
                    await c.bot.edit_message_text(
                        chat_id=cid,
                        message_id=msg_id,
                        text=ttt_build_text(g),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=ttt_build_keyboard(g["board"], disabled=True)
                    )
                except:
                    pass
                for uid in [str(g.get("x_id","")), str(g.get("o_id",""))]:
                    user_in_game.pop(uid, None)
                game_timers.pop(gkey, None)
                ttt_games.pop(gkey, None)
                return
            
            try:
                await c.bot.edit_message_text(
                    chat_id=cid,
                    message_id=msg_id,
                    text=ttt_build_text(g),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=ttt_build_keyboard(g["board"])
                )
            except:
                pass
    except asyncio.CancelledError:
        pass

def player_busy(uid: str) -> bool:
    gkey = user_in_game.get(uid)
    if not gkey:
        return False
    if gkey in ttt_games:
        return True
    user_in_game.pop(uid, None)
    return False

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
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
                    await u.message.reply_text("⚠️ Player in game!")
                    return
        
        if player_busy(uid_a):
            await u.message.reply_text("⚠️ You're in a game!")
            return
        
        board = [TTT_EMPTY] * 9
        g = {
            "board": board, "turn": "X", "x_id": ua.id, "x_name": name_a,
            "o_id": user_b_id if not vs_bot else -1, "o_name": name_b,
            "vs_bot": vs_bot, "status": "waiting" if not vs_bot else "playing",
            "created": time.time(), "chat_id": cid, "msg_id": None
        }
        
        if vs_bot:
            msg = await u.message.reply_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
            g.update({"status": "playing", "msg_id": msg.message_id})
            gkey = game_key(msg.message_id, cid)
            ttt_games[gkey] = g
            game_timers[gkey] = {"remaining": 300}
            user_in_game[uid_a] = gkey
            asyncio.create_task(run_game_timer(c, gkey))
        else:
            msg = await u.message.reply_text(
                f"🎮 *TIC TAC TOE — LOBBY*\n❌ {g['x_name']}: ⏳ Waiting\n⭕ {g['o_name']}: ⏳ Waiting\n\n_Both press READY!_",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("READY 🔥", callback_data=f"ttt_ready:temp")]])
            )
            g["msg_id"] = msg.message_id
            gkey = game_key(msg.message_id, cid)
            ttt_games[gkey] = g
            user_in_game[uid_a], user_in_game[str(user_b_id)] = gkey, gkey
        
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[tictac] {e}")

async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        try:
            await q.answer()
        except:
            pass
        
        parts = q.data.split(":")
        action, idx = parts[1], int(parts[2]) if len(parts) > 2 else -1
        cid, mid = q.message.chat_id, q.message.message_id
        gkey = game_key(mid, cid)
        g = ttt_games.get(gkey)
        
        if not g or g["status"] != "playing" or action == "noop":
            return
        
        uid = str(q.from_user.id)
        if g["turn"] == "X" and uid != str(g["x_id"]):
            return
        if g["turn"] == "O" and not g["vs_bot"] and uid != str(g["o_id"]):
            return
        
        board = g["board"]
        if idx < 0 or idx >= 9 or board[idx] != TTT_EMPTY:
            return
        
        if gkey in game_timers:
            game_timers[gkey]["remaining"] = 300
        
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        ws = ttt_check_winner(board)
        
        if ws:
            g["status"], g["winner_name"] = "win", (g["x_name"] if ws == TTT_X else g["o_name"])
            try:
                await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except:
                pass
            for uid in [str(g["x_id"]), str(g["o_id"])]:
                user_in_game.pop(uid, None)
            game_timers.pop(gkey, None)
            ttt_games.pop(gkey, None)
            return
        
        if ttt_is_draw(board):
            g["status"] = "draw"
            try:
                await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except:
                pass
            for uid in [str(g["x_id"]), str(g["o_id"])]:
                user_in_game.pop(uid, None)
            game_timers.pop(gkey, None)
            ttt_games.pop(gkey, None)
            return
        
        g["turn"] = "O" if g["turn"] == "X" else "X"
        
        if g["vs_bot"] and g["turn"] == "O":
            bi = ttt_bot_move(board)
            if bi >= 0:
                board[bi] = TTT_O
                ws2 = ttt_check_winner(board)
                if ws2 or ttt_is_draw(board):
                    g["status"] = "win" if ws2 else "draw"
                    if ws2:
                        g["winner_name"] = (g["x_name"] if ws2 == TTT_X else g["o_name"])
                    try:
                        await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
                    except:
                        pass
                    user_in_game.pop(str(g["x_id"]), None)
                    game_timers.pop(gkey, None)
                    ttt_games.pop(gkey, None)
                    return
                g["turn"] = "X"
        
        try:
            await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        except:
            pass
    except Exception as e:
        logger.error(f"[ttt_cb] {e}")

# ═══════════════════════════════════════════════════════════════
# MINESWEEPER (Minimal Implementation)
# ═══════════════════════════════════════════════════════════════
def _mine_setup_keyboard(gkey: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("3", callback_data=f"mine:set:{gkey}:3"),
         InlineKeyboardButton("4", callback_data=f"mine:set:{gkey}:4"),
         InlineKeyboardButton("5", callback_data=f"mine:set:{gkey}:5")]
    ])

def _mine_board_keyboard(gkey: str, state: list, revealed: list, disabled: bool = False) -> InlineKeyboardMarkup:
    rows, r = [], []
    for i in range(6):
        if disabled or revealed[i]:
            label = "💣" if state[i] else ("✅" if revealed[i] else "⬜")
        else:
            label = "📦"
        btn = InlineKeyboardButton(label, callback_data=f"mine:play:{gkey}:{i}" if not disabled and not revealed[i] else f"mine:noop:{gkey}:{i}")
        r.append(btn)
        if len(r) == 3:
            rows.append(r)
            r = []
    if r:
        rows.append(r)
    return InlineKeyboardMarkup(rows)

def mine_build_text(g: dict, rem: int) -> str:
    bombs, total_safe, opened = g["bombs"], 6 - g["bombs"], sum(1 for x in g["revealed"] if x)
    if g.get("status") == "timeout":
        return "⏰ *Time Up!*\n\nLost *-5 pts*."
    elif g.get("status") == "lost":
        return "💥 *BOOM!*\n\nLost *-5 pts*."
    elif g.get("status") == "won":
        return f"🎉 *YOU WIN!*\n\nAll {total_safe} safe boxes found! Won *+700 pts*."
    else:
        return f"💣 *MINESWEEPER*\nMines: {bombs}  |  Safe: {opened}/{total_safe}\n⏱ Time: `{rem}s`"

async def mine_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
        now = time.time()
        m_stat = mine_play_stats.setdefault(uid, {"plays": 0, "block_until": 0})
        
        if now < m_stat["block_until"]:
            rem_m = max(1, int((m_stat["block_until"] - now) // 60))
            await u.message.reply_text(f"⏳ *Cooldown!*\nWait {rem_m} minutes.")
            return
        
        m_stat["plays"] += 1
        if m_stat["plays"] > 20:
            m_stat["block_until"] = now + 3600
            m_stat["plays"] = 0
            await u.message.reply_text("🛑 *Limit Hit!*\n1-hour break.")
            return
        
        gkey = f"{cid}_{uid}_{int(now)}"
        mine_games[gkey] = {
            "uid": uid, "name": (u.effective_user.first_name or "Player")[:20],
            "bombs": 0, "state": [], "revealed": [False]*6,
            "chat_id": u.effective_chat.id, "msg_id": None, "status": "setting"
        }
        msg = await u.message.reply_photo(
            photo=MINE_IMAGE_URL,
            caption="💣 *MINESWEEPER*\n\nChoose mines:",
            reply_markup=_mine_setup_keyboard(gkey)
        )
        mine_games[gkey]["msg_id"] = msg.message_id
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[mine] {e}")

async def run_mine_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    try:
        while True:
            await asyncio.sleep(5)
            g, td = mine_games.get(gkey), mine_timers.get(gkey)
            if not g or not td or g.get("status") != "playing":
                return
            
            td["remaining"] = max(0, td["remaining"] - 5)
            cid, msg_id = g.get("chat_id"), g.get("msg_id")
            
            if not msg_id:
                return
            
            if td["remaining"] <= 0:
                g["status"] = "timeout"
                new_sc = await update_score(str(cid), g["uid"], g["name"], -5)
                try:
                    await c.bot.edit_message_caption(
                        chat_id=cid, message_id=msg_id,
                        caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True)
                    )
                except:
                    pass
                mine_timers.pop(gkey, None)
                mine_games.pop(gkey, None)
                return
            
            try:
                await c.bot.edit_message_caption(
                    chat_id=cid, message_id=msg_id,
                    caption=mine_build_text(g, td["remaining"]),
                    reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"])
                )
            except:
                pass
    except asyncio.CancelledError:
        pass

async def mine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        try:
            await q.answer()
        except:
            pass
        
        parts = q.data.split(":")
        action, gkey, val = parts[1], parts[2], int(parts[3])
        
        if gkey not in mine_games:
            return
        
        g = mine_games[gkey]
        if str(q.from_user.id) != g["uid"]:
            await q.answer("Not your game!")
            return
        
        if action == "noop":
            return
        
        if action == "set":
            if g.get("status") != "setting":
                return
            bombs = max(3, min(5, val))
            state = [True]*bombs + [False]*(6-bombs)
            random.shuffle(state)
            g.update({"bombs": bombs, "state": state, "status": "playing", "revealed": [False]*6})
            mine_timers[gkey] = {"remaining": 60}
            asyncio.create_task(run_mine_timer(context, gkey))
            try:
                await q.edit_message_caption(caption=mine_build_text(g, 60), reply_markup=_mine_board_keyboard(gkey, state, g["revealed"]))
            except:
                pass
        elif action == "play":
            if g.get("status") != "playing" or g["revealed"][val]:
                return
            
            is_bomb = g["state"][val]
            cid = str(q.message.chat_id)
            
            if is_bomb:
                g["status"] = "lost"
                mine_timers.pop(gkey, None)
                new_sc = await update_score(cid, g["uid"], g["name"], -5)
                try:
                    await q.edit_message_caption(
                        caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*",
                        reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True)
                    )
                except:
                    pass
                mine_games.pop(gkey, None)
            else:
                g["revealed"][val] = True
                total_safe = 6 - g["bombs"]
                opened_count = sum(1 for x in g["revealed"] if x)
                
                if gkey in mine_timers:
                    mine_timers[gkey]["remaining"] = 60
                
                if opened_count >= total_safe:
                    g["status"] = "won"
                    mine_timers.pop(gkey, None)
                    new_sc = await update_score(cid, g["uid"], g["name"], +700)
                    try:
                        await q.edit_message_caption(
                            caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*",
                            reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True)
                        )
                    except:
                        pass
                    mine_games.pop(gkey, None)
                else:
                    rem = mine_timers.get(gkey, {}).get("remaining", 60)
                    try:
                        await q.edit_message_caption(
                            caption=mine_build_text(g, rem),
                            reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"])
                        )
                    except:
                        pass
    except Exception as e:
        logger.error(f"[mine_callback] {e}")

# ═══════════════════════════════════════════════════════════════
# SEARCH & BANANALOGIC WITH TELEGRAM QUOTE
# ═══════════════════════════════════════════════════════════════
def wiki_summary(query: str) -> dict:
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

def google_search(query: str) -> dict:
    out = {"found": False, "ai_answer": "", "snippets": []}
    try:
        r = requests.get(f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=8&hl=en", headers=G_HDR, timeout=10)
        if r.status_code != 200: return out
        html = r.text
        for pat in [r'data-attrid="wa:/description"[^>]*>[\s\S]{0,200}?<span[^>]*>([^<]{40,800})', r'<div class="BNeawe s3v9rd AP7Wnd">([\s\S]{40,800}?)</div>']:
            m = re.search(pat, html, re.DOTALL)
            if m: c2 = clean_html(m.group(1)); out["ai_answer"] = c2[:800]; break
        seen = set()
        for m in re.finditer(r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,350}?)</div', html, re.DOTALL):
            t = clean_html(m.group(1))
            if len(t) > 40 and t not in seen: seen.add(t); out["snippets"].append(t[:300])
            if len(out["snippets"]) >= 5: break
        out["found"] = bool(out["ai_answer"] or out["snippets"])
    except: pass
    return out

async def web_summarise(query: str, wiki: dict, goog: dict, system_prompt: str, max_tok: int = 500) -> str:
    ctx = []
    if goog["ai_answer"]: ctx.append(f"Google Featured Answer: {goog['ai_answer']}")
    if goog["snippets"]: ctx.append("Web snippets:\n" + "\n".join(f"- {s}" for s in goog["snippets"]))
    if wiki["found"]: ctx.append(f"Wikipedia Context ({wiki['title']}):\n{wiki['intro']}")
    if not ctx: return ""
    return await ai(system_prompt, f"User question: {query}\n\nSearch facts:\n{chr(10).join(ctx)[:3000]}\n\nAnswer concisely based on facts.", "", max_tok=max_tok)

async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await u.message.reply_text("🐱 Usage: `/search query`")
        return
    query = parts[1].strip()
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "🔍")
    sm = await u.message.reply_text("🔎 *Web Search*\n*Gathering data...*", parse_mode=ParseMode.MARKDOWN)
    loop = asyncio.get_running_loop()
    wiki, goog = await asyncio.gather(loop.run_in_executor(None, wiki_summary, query), loop.run_in_executor(None, google_search, query))
    summary = await web_summarise(query, wiki, goog, "Smart assistant. Write a clean concise summary. Max 250 words.")
    if summary:
        await sm.delete()
        await u.message.reply_text(
            f"🔍 *{query}*\n\n{summary}",
            parse_mode=ParseMode.MARKDOWN,
            reply_to_message_id=u.message.message_id
        )
    else:
        await sm.edit_text("😿 No results found.")

async def bananalogic_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await u.message.reply_text("🍌 Usage: `/bananalogic query`")
        return
    query = parts[1].strip()
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "🍌")
    sm = await u.message.reply_text("🍌 *BananaLogic*\n*Scraping web...*", parse_mode=ParseMode.MARKDOWN)
    loop = asyncio.get_running_loop()
    wiki, goog = await asyncio.gather(loop.run_in_executor(None, wiki_summary, query), loop.run_in_executor(None, google_search, query))
    answer = await web_summarise(query, wiki, goog, BANANA_PROMPT, max_tok=600)
    if answer:
        await sm.delete()
        await u.message.reply_text(
            f"🍌 *BananaLogic*\n\n{answer}",
            parse_mode=ParseMode.MARKDOWN,
            reply_to_message_id=u.message.message_id
        )
    else:
        await sm.edit_text("🍌 No response. Try again!")

# ═══════════════════════════════════════════════════════════════
# GENERAL CHAT & SENTIMENT
# ═══════════════════════════════════════════════════════════════
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Main chat monitor - handles mentions, reactions, sentiment, memory"""
    if not u.message or not u.effective_user or u.effective_user.is_bot:
        return
    
    try:
        uid, cid, now = u.effective_user.id, str(u.effective_chat.id), datetime.now()
        
        # Anti-spam
        spam_tracker.setdefault(uid, [])
        spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
        spam_tracker[uid].append(now)
        
        if len(spam_tracker[uid]) >= 4:
            try:
                await u.message.delete()
            except:
                pass
            return
        
        # Track active users
        db.setdefault("seen",{}).setdefault(cid,{})[str(uid)] = {
            "id": uid, "un": u.effective_user.username, "n": u.effective_user.first_name or "User"
        }
        
        counts = db.setdefault("counts", {})
        counts[cid] = counts.get(cid, 0) + 1
        
        # Sentiment-based random reactions (every 6th message)
        if counts[cid] % 6 == 0:
            text = (u.message.text or u.message.caption or "").strip()
            sentiment, emoji = analyze_sentiment(text)
            try:
                await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
            except:
                pass
        
        text = (u.message.text or u.message.caption or "").strip()
        
        # Ignore commands
        if text.startswith("/"):
            return
        
        bot_username = bot_status.get("username", "")
        text_low = text.lower()
        contains_beluga = "beluga" in text_low
        contains_username = bool(bot_username) and (bot_username in text_low or f"@{bot_username}" in text_low)
        is_reply = u.message.reply_to_message and u.message.reply_to_message.from_user and u.message.reply_to_message.from_user.id == c.bot.id
        
        # Save chat memory
        if text and len(text) > 10:
            await save_chat_memory(cid, str(uid), u.effective_user.first_name or "User", text)
        
        # Check if user is asking about media (image analysis)
        if u.message.reply_to_message and (u.message.reply_to_message.photo or u.message.reply_to_message.video or u.message.reply_to_message.sticker):
            await analyze_image_with_cv(u, c)
            return
        
        # Chat with bot
        if text and (contains_beluga or contains_username or is_reply):
            try:
                await asyncio.wait_for(c.bot.send_chat_action(u.effective_chat.id, "typing"), timeout=4.0)
            except:
                pass
            
            emoji = await ai_emoji(text)
            try:
                await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
            except:
                pass
            
            system_prompt = process_linguistic_sentiment_analysis(text)
            reply = "Meow! 🐾"
            try:
                reply = await ai(system_prompt, text, "Meow! 🐾")
            except:
                pass
            
            try:
                # Use quote feature in reply
                await u.message.reply_text(reply, reply_to_message_id=u.message.message_id)
            except:
                pass
        
        # Detect language and respond in user's language
        try:
            user_lang = detect(text)
            if user_lang not in ["en", "hi"]:
                # Try to respond in English + Hinglish
                pass
        except:
            pass
        
        bot_status["message_count"] += 1
        bot_status["last_update"] = datetime.now()
    except Exception as e:
        logger.error(f"[monitor] {e}")
        bot_status["error_count"] += 1

# ═══════════════════════════════════════════════════════════════
# HTTP HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({
        "status": "healthy", "uptime_seconds": up, "running": bot_status["running"],
        "messages": bot_status["message_count"], "version": "11.0.0"
    })

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()})

async def start_http(port: int):
    aio = web.Application()
    aio.router.add_get("/", _ping)
    aio.router.add_get("/health", _health)
    aio.router.add_get("/ping", _ping)
    runner = web.AppRunner(aio)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ HTTP API @ 0.0.0.0:{port}")
    return runner

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut, Forbidden)):
        return
    if isinstance(err, RetryAfter):
        await asyncio.sleep(err.retry_after + 1)
        return
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return
    
    bot_status["error_count"] += 1
    tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logger.error(f"[Err] {err}\n{tb_str}")
    
    if OWNER_ID:
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"⚠️ *Error:* `{str(err)[:150]}`",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

# ═══════════════════════════════════════════════════════════════
# START & HELP (Stylish Design)
# ═══════════════════════════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    
    text = """✨ *BELUGA QUANT BOT v11.0.0* ✨
🐱 Your friendly AI crypto companion from Team Oldy Crypto!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎮 *GAMES*
  ├─ `/quiz` [topic]  — Brain Trivia Quiz
  ├─ `/tictac` [@user]  — Tic Tac Toe
  ├─ `/mine`  — Minesweeper Challenge
  └─ `/gay` `/couple`  — Daily Rainbow & Couple

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 *CRYPTO (Live Prices)*
  ├─ `/price` <ticker>  — BTC, ETH, SOL, etc.
  ├─ `/topgainers`  — Top 5 Gainers (24h)
  ├─ `/toplosers`  — Top 5 Losers (24h)
  └─ `/chart` <ticker> [5m|15m|1h|4h|1d]  — Candlestick Charts

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📰 *NEWS (Latest Updates)*
  ├─ `/news`  — Crypto Headlines
  ├─ `/ainews`  — AI & ML News
  └─ `/technews`  — Tech News

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 *SEARCH & AI*
  ├─ `/search` <query>  — Web Search
  ├─ `/bananalogic` <query>  — AI Web Analysis
  └─ Just @ mention me — Chat with Beluga

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🖼️ *IMAGE TOOLS*
  ├─ `/qr` <text>  — QR Code Generator
  ├─ `/scanqr`  — Scan QR Codes
  ├─ `/resize`  — Resize to 512x512
  ├─ `/compress`  — Compress Image
  ├─ `/watermark` [text]  — Add Watermark
  └─ `/imginfo`  — Image Details

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏆 *LEADERBOARD & STATS*
  ├─ `/lb`  — View Leaderboard
  ├─ `/gm`  — Good Morning Check-in
  ├─ `/nw`  — New Week (Admin)
  ├─ `/pump` <pts>  — Add Points (Admin)
  └─ `/dump` <pts>  — Remove Points (Admin)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔥 *FEATURES*
  🤖 Advanced Computer Vision  — Ask about images!
  💾 AI Memory System  — Beluga remembers you
  😊 Sentiment Analysis  — Emotional reactions
  🔗 Telegram Quote Feature  — Smart replies
  🌍 Multi-language Support  — English + Hinglish
  🚀 No API Keys  — CCXT Crypto Only
  💿 GitHub Backed  — Data Persistence

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Just reply to any image/video and ask:
"What's in this?" 👀🐾

Have fun! 🎉"""
    
    await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ═══════════════════════════════════════════════════════════════
# MAIN BOT SETUP
# ═══════════════════════════════════════════════════════════════
async def main():
    logger.info("🐱 INITIALIZING BELUGA BOT v11.0.0")
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)
    
    app = TGApp.builder().token(BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    
    # Crypto commands
    app.add_handler(CommandHandler("price", crypto_price_handler))
    app.add_handler(CommandHandler(["topgainers","toplosers"], crypto_movers_handler))
    app.add_handler(CommandHandler(["chart","chart5m","chart15m","chart1h","chart4h","chart1d"], crypto_chart_handler))
    
    # News commands
    app.add_handler(CommandHandler("news", lambda u, c: execute_news_flow(u, c, "crypto", "Crypto News")))
    app.add_handler(CommandHandler("ainews", lambda u, c: execute_news_flow(u, c, "ai", "AI News")))
    app.add_handler(CommandHandler("technews", lambda u, c: execute_news_flow(u, c, "tech", "Tech News")))
    
    # Search & AI
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("bananalogic", bananalogic_handler))
    
    # Image tools
    app.add_handler(CommandHandler("qr", qr_generate_handler))
    app.add_handler(CommandHandler("scanqr", qr_scan_handler))
    app.add_handler(CommandHandler("resize", lambda u, c: img_handler(u, c, "resize")))
    app.add_handler(CommandHandler("compress", lambda u, c: img_handler(u, c, "compress")))
    app.add_handler(CommandHandler("watermark", lambda u, c: img_handler(u, c, "watermark")))
    app.add_handler(CommandHandler("imginfo", lambda u, c: img_handler(u, c, "info")))
    
    # Quiz & Games
    app.add_handler(CommandHandler("quiz", quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"], lb_handler))
    app.add_handler(CommandHandler("nw", nw_handler))
    app.add_handler(CommandHandler(["pump","dump"], pump_dump_handler))
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("mine", mine_handler))
    app.add_handler(CommandHandler("gm", gm_handler))
    
    # Fun commands (24h persistence, all members)
    app.add_handler(CommandHandler(["gay","couple"], fun_dispatcher))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(ttt_callback, pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(gm_callback, pattern=r"^gm:"))
    app.add_handler(CallbackQueryHandler(mine_callback, pattern=r"^mine:"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    
    # Chat monitor
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Initialize
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
    except:
        pass
    
    cleanup_task = asyncio.create_task(cleanup_expired_games())
    sync_task = asyncio.create_task(periodic_github_sync())
    
    await stop_evt.wait()
    
    cleanup_task.cancel()
    sync_task.cancel()
    
    bot_status["running"] = False
    
    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try:
            await fn()
        except:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        sys.exit(1)
