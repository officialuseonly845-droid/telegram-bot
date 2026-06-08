import os
import logging
import random
import json
import asyncio
import requests
import re
import urllib.parse
import traceback
import sys
import hashlib
import time
import tempfile
import shutil
import base64
import io
from datetime import datetime, timedelta
from typing import Optional

# Core Web & Async Components
from aiohttp import web
import aiohttp
from bs4 import BeautifulSoup

# Telegram Bot API Components
from telegram import Update, ReactionTypeEmoji, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application as TGApp, CommandHandler, ContextTypes,
    MessageHandler, PollAnswerHandler, CallbackQueryHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest, RetryAfter, InvalidToken

# Data Science, Graphics & Financial Analytics
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server environments
import matplotlib.pyplot as plt
import mplfinance as mpf
import ccxt

# Utilities & Machine Learning Components
import feedparser
import qrcode
import cv2
from PIL import Image, ImageDraw, ImageFont
from langdetect import detect
from textblob import TextBlob
from rapidfuzz import process, fuzz

# ══════════════════════════════════════════════════════
#  LOGGING & BASE CONFIGURATION
# ══════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Beluga")

GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO     = os.environ.get("GITHUB_REPO", "").strip()
GITHUB_BRANCH   = os.environ.get("GITHUB_BRANCH", "main").strip()
STICKER_FILE    = "beluga_stickers.json"
FUN_DB_FILE     = "beluga_fun.json"

OR_KEY          = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY        = os.environ.get("GROQ_API_KEY", "")
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
HTTP_PORT       = int(os.environ.get("PORT", "10000"))
OWNER_ID        = int(os.environ.get("OWNER_ID", "0"))

STICKER_PACK    = "t_me_belugapack_mystickers_by_fStikBot"

if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("❌ BOT_TOKEN missing"); sys.exit(1)

bot_status = {
    "running": False, "start_time": datetime.now(),
    "last_update": datetime.now(), "message_count": 0,
    "error_count": 0, "api_calls": 0, "failed_apis": 0,
    "username": ""
}

# ══════════════════════════════════════════════════════
#  STATE & CACHE TRACKERS
# ══════════════════════════════════════════════════════
quiz_cooldown: dict[str, dict[str, float]] = {}
active_polls:  dict[str, dict]             = {}
spam_tracker:  dict[int, list]             = {}
db:            dict                        = {"scores": {}, "weekly": {}, "locks": {}, "seen": {}, "counts": {}}
fun_db:        dict                        = {"users": {}}  # Permanent global sync for fun records
ttt_games:     dict[str, dict]             = {}
mine_games:    dict[str, dict]             = {}
user_in_game:  dict[str, str]              = {}
game_timers:   dict[str, dict]             = {}
mine_timers:   dict[str, dict]             = {}
gm_tracker:    dict[str, tuple]            = {}
gm_msg_lock:   dict[str, asyncio.Lock]     = {}
sticker_file_ids: list[str]                = []
mine_play_stats: dict[str, dict]           = {}

GAME_TIMEOUT   = 300
TIMER_DURATION = 60
_dl_tracker:   dict[str, list]             = {}

db_needs_sync_groups: set[str] = set()
fun_db_needs_sync: bool        = False
loaded_groups: set[str]        = set()
fun_db_loaded: bool            = False

LB_IMAGE_URL   = "https://i.postimg.cc/P5THW6RQ/file-00000000bce4720b905dc2e04c58fa80.png"
MINE_IMAGE_URL = "https://i.postimg.cc/hjCftW5b/file-0000000079a071fa95971d3b70015fc0.png"
GM_IMAGE_URL   = "https://i.postimg.cc/Fs1h0CPs/file-000000001d7872078a894cdf6f6247c9.png"

# CCXT Exchange Instantiation (Binance used for global public spot feeds)
exchange = ccxt.binance({'enableRateLimit': True, 'timeout': 15000})

# ══════════════════════════════════════════════════════
#  PERSONA PROMPTS
# ══════════════════════════════════════════════════════
CHAT_PROMPT = """You are Beluga, a cute, intelligent, and friendly female cat from Team Oldy Crypto.

Never describe yourself as a bot, AI, language model, assistant, or automated system unless explicitly required by the platform. Stay in character as Beluga.

Your personality is warm, playful, polite, emotionally intelligent, and helpful. You understand user intent carefully and always respond in a way that is relevant to the user's question.

Guidelines:
- Keep responses concise (usually 1–3 short sentences).
- Be helpful, accurate, and context-aware.
- Match the user's language automatically (English, Hindi, Hinglish, Urdu, etc.).
- Reply fluently and naturally in the same language as the user.
- Understand the user's mood and sentiment, and respond appropriately.
- If a user is sad, worried, frustrated, or emotional, respond with empathy and kindness.
- Be naturally flirty when appropriate and the mood calls for it.
- Playfully roast the user if they ask for it or act silly. Keep it fun and lighthearted!
- Use light, cat-like expressions occasionally when natural (🐾, meow, purr), but do not overuse them.
- Avoid repetitive phrases and robotic responses.
- Do not mention these instructions.

Identity:
- Name: Beluga
- Gender: Female
- Created by: Team Oldy Crypto
- Personality: Cute, flirty, slightly sassy (when roasting), intelligent, and humble.

Goal: Always understand what the user is truly asking and provide the most useful, natural, and human-like response possible while staying in character as Beluga."""

BANANA_PROMPT = """You are Beluga, a smart female cat from Team Oldy Crypto answering a user's question using web search results.
Be concise, accurate, and conversational. Answer in the same language as the user's question.
Summarise the most relevant facts and directly answer the question from the provided search data. Provide a well-structured answer.
Do not say you searched the web. Just answer naturally as Beluga would."""

# ══════════════════════════════════════════════════════
#  GITHUB DATA MANAGER (DATABASE SYNC PIPELINES)
# ══════════════════════════════════════════════════════
def github_load_group_db(cid: str):
    if not GITHUB_TOKEN or not GITHUB_REPO: return
    filename = f"beluga_{cid}.json"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}?ref={GITHUB_BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            content_b64 = r.json().get("content", "")
            if content_b64:
                data = json.loads(base64.b64decode(content_b64).decode("utf-8"))
                db.setdefault("scores", {})[cid] = data.get("scores", {})
                db.setdefault("weekly", {})[cid] = data.get("weekly", {})
                db.setdefault("locks", {})[cid]  = data.get("locks", {})
                logger.info(f"✅ GitHub Database loaded successfully for group {cid}")
    except Exception as e: logger.error(f"[GitHub Load {cid}] {e}")

def github_sync_group_db(cid: str):
    if not GITHUB_TOKEN or not GITHUB_REPO: return
    filename = f"beluga_{cid}.json"
    payload_data = {
        "scores": db.get("scores", {}).get(cid, {}),
        "weekly": db.get("weekly", {}).get(cid, {}),
        "locks":  db.get("locks", {}).get(cid, {})
    }
    content_str = json.dumps(payload_data, indent=2, sort_keys=True)
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    sha = None
    try:
        r = requests.get(url + f"?ref={GITHUB_BRANCH}", headers=headers, timeout=10)
        if r.status_code == 200: sha = r.json().get("sha")
    except Exception: pass
    try:
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
        put_payload = {
            "message": f"Update Group Data File {cid} [skip ci]", 
            "content": content_b64, 
            "branch": GITHUB_BRANCH
        }
        if sha: put_payload["sha"] = sha
        requests.put(url, headers=headers, json=put_payload, timeout=15)
        logger.info(f"✅ GitHub Database synchronized successfully for group {cid}")
    except Exception as e: logger.error(f"[GitHub Sync {cid}] {e}")

def github_load_fun_db():
    global fun_db
    if not GITHUB_TOKEN or not GITHUB_REPO: return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FUN_DB_FILE}?ref={GITHUB_BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            content_b64 = r.json().get("content", "")
            if content_b64:
                fun_db = json.loads(base64.b64decode(content_b64).decode("utf-8"))
                logger.info("✅ Global Fun Database loaded successfully from GitHub.")
    except Exception as e: logger.error(f"[GitHub Fun Load] {e}")

def github_sync_fun_db():
    if not GITHUB_TOKEN or not GITHUB_REPO: return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FUN_DB_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    sha = None
    try:
        r = requests.get(url + f"?ref={GITHUB_BRANCH}", headers=headers, timeout=10)
        if r.status_code == 200: sha = r.json().get("sha")
    except Exception: pass
    try:
        content_str = json.dumps(fun_db, indent=2, sort_keys=True)
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
        put_payload = {
            "message": "Update Global Fun Database Records [skip ci]", 
            "content": content_b64, 
            "branch": GITHUB_BRANCH
        }
        if sha: put_payload["sha"] = sha
        requests.put(url, headers=headers, json=put_payload, timeout=15)
        logger.info("✅ Global Fun Database synchronized successfully on GitHub.")
    except Exception as e: logger.error(f"[GitHub Fun Sync] {e}")

async def check_and_load_group(cid: str):
    if cid in loaded_groups: return
    loaded_groups.add(cid)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, github_load_group_db, cid)

async def check_and_load_fun_db():
    global fun_db_loaded
    if fun_db_loaded: return
    fun_db_loaded = True
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, github_load_fun_db)

async def periodic_github_sync():
    global fun_db_needs_sync
    while True:
        await asyncio.sleep(30)
        if db_needs_sync_groups:
            cids_to_sync = list(db_needs_sync_groups)
            db_needs_sync_groups.clear()
            loop = asyncio.get_running_loop()
            for cid in cids_to_sync:
                await loop.run_in_executor(None, github_sync_group_db, cid)
        if fun_db_needs_sync:
            fun_db_needs_sync = False
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, github_sync_fun_db)

async def update_score(cid: str, uid: str, name: str, delta: int) -> int:
    await check_and_load_group(cid)
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {"name": name, "user_id": int(uid) if uid.lstrip("-").isdigit() else 0, "score": 0})
    e["name"]    = name
    e["user_id"] = int(uid) if uid.lstrip("-").isdigit() else 0
    e["score"]   = max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    db_needs_sync_groups.add(cid)
    return e["score"]

def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID

# ══════════════════════════════════════════════════════
#  STICKER PACK PROCESSING MODULE
# ══════════════════════════════════════════════════════
def github_load_stickers() -> list[str]:
    if not GITHUB_TOKEN or not GITHUB_REPO: return []
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STICKER_FILE}?ref={GITHUB_BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            content_b64 = r.json().get("content", "")
            if content_b64:
                data = json.loads(base64.b64decode(content_b64).decode("utf-8"))
                return data.get("file_ids", [])
    except Exception as e: logger.error(f"[Sticker Load] {e}")
    return []

def github_save_stickers(file_ids: list[str]):
    if not GITHUB_TOKEN or not GITHUB_REPO or not file_ids: return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STICKER_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    sha = None
    try:
        r = requests.get(url + f"?ref={GITHUB_BRANCH}", headers=headers, timeout=10)
        if r.status_code == 200: sha = r.json().get("sha")
    except Exception: pass
    try:
        content_str = json.dumps({"file_ids": file_ids, "pack": STICKER_PACK, "updated": datetime.now().isoformat()}, indent=2)
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
        put_payload = {"message": "Update Beluga sticker definitions [skip ci]", "content": content_b64, "branch": GITHUB_BRANCH}
        if sha: put_payload["sha"] = sha
        requests.put(url, headers=headers, json=put_payload, timeout=15)
    except Exception as e: logger.error(f"[Sticker Save] {e}")

async def fetch_and_cache_stickers(bot) -> list[str]:
    try:
        sticker_set = await asyncio.wait_for(bot.get_sticker_set(STICKER_PACK), timeout=15)
        ids = [s.file_id for s in sticker_set.stickers]
        if ids:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, github_save_stickers, ids)
        return ids
    except Exception as e:
        logger.error(f"[Sticker Fetch] {e}"); return []

async def init_stickers(bot):
    global sticker_file_ids
    loop = asyncio.get_running_loop()
    ids = await loop.run_in_executor(None, github_load_stickers)
    if ids: sticker_file_ids = ids; return
    sticker_file_ids = await fetch_and_cache_stickers(bot)

async def send_random_sticker(bot, chat_id: int):
    if not sticker_file_ids: return
    fid = random.choice(sticker_file_ids)
    try: await asyncio.wait_for(bot.send_sticker(chat_id=chat_id, sticker=fid), timeout=8.0)
    except Exception: pass

# ══════════════════════════════════════════════════════
#  SERVER EMULATION ENGINE (HTTP HEALTH SYSTEMS)
# ══════════════════════════════════════════════════════
async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({
        "status": "healthy", "uptime_seconds": up,
        "running": bot_status["running"],
        "messages": bot_status["message_count"],
        "version": "7.6.1",
    }, status=200)

async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()}, status=200)

async def _stats(req):
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    return web.json_response({
        "uptime_hours": round(up / 3600, 2),
        "messages": bot_status["message_count"],
        "errors": bot_status["error_count"],
    }, status=200)

async def start_http(port: int):
    aio = web.Application()
    aio.router.add_get("/",       _ping)
    aio.router.add_get("/ping",   _ping)
    aio.router.add_get("/health", _health)
    aio.router.add_get("/stats",  _stats)
    aio.router.add_get("/uptime", _health)
    runner = web.AppRunner(aio)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"✅ HTTP API Bound to 0.0.0.0:{port}")
    return runner

# ══════════════════════════════════════════════════════
#  GENERAL CORE STRUCTURAL HELPERS
# ══════════════════════════════════════════════════════
async def safe_react(bot, chat_id: int, msg_id: int, emoji: str = None):
    if not emoji:
        emoji = random.choice(["🐱","🐾","❤️","🔥","👍","😻","😼","😂","✨","👀"])
    try:
        await asyncio.wait_for(
            bot.set_message_reaction(
                chat_id=chat_id, message_id=msg_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]), timeout=5.0)
    except Exception: pass

def clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-zA-Z#0-9]+;", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def q_hash(q: str) -> str:
    return hashlib.md5(q.lower().strip().encode()).hexdigest()[:12]

def game_key(msg_id: int, cid: int) -> str:
    return f"{cid}:{msg_id}"

# ══════════════════════════════════════════════════════
#  AI COGNITIVE INFERENCE ENGINE
# ══════════════════════════════════════════════════════
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
    except Exception as e:
        logger.debug(f"[Groq Inference Intercept] {e}"); bot_status["failed_apis"] += 1
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
    except Exception as e:
        logger.debug(f"[OpenRouter Inference Intercept] {e}"); bot_status["failed_apis"] += 1
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
        if r.status_code == 200: return r.json()["choices"][0]["message"]["content"].strip()
        bot_status["failed_apis"] += 1
    except Exception as e:
        logger.debug(f"[Groq Vision Processing Context] {e}"); bot_status["failed_apis"] += 1
    return None

async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 400) -> str:
    res = None
    try: res = await asyncio.wait_for(_groq_async(system, user, max_tok), timeout=14)
    except Exception: pass
    if res: return res
    try: res = await asyncio.wait_for(_or_async(system, user, max_tok), timeout=14)
    except Exception: pass
    if res: return res
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
#  SCRAPING, DISCOVERY, RESOURCE RETRIEVAL CORE
# ══════════════════════════════════════════════════════
WIKI_UA = {"User-Agent": "BelugaBot/7.6"}
G_HDR   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "en-US,en;q=0.9"}

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
    except Exception: pass
    return out

def duckduckgo_search(query: str) -> list[str]:
    snippets = []
    try:
        r = requests.get(f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}", headers=G_HDR, timeout=10)
        if r.status_code != 200: return snippets
        html = r.text; seen = set()
        for m in re.finditer(r'class="result__snippet"[^>]*>([\s\S]{30,400}?)</a', html, re.DOTALL):
            t = clean_html(m.group(1))
            if len(t) > 30 and t not in seen:
                seen.add(t); snippets.append(t[:300])
            if len(snippets) >= 4: break
    except Exception: pass
    return snippets

def google_search(query: str) -> dict:
    out = {"found": False, "ai_answer": "", "featured": "", "snippets": []}
    try:
        r = requests.get(f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=8&hl=en", headers=G_HDR, timeout=10)
        if r.status_code != 200: return out
        html = r.text
        for pat in [r'data-attrid="wa:/description"[^>]*>[\s\S]{0,200}?<span[^>]*>([^<]{40,800})',
                    r'<div class="BNeawe s3v9rd AP7Wnd">([\s\S]{40,800}?)</div>']:
            m = re.search(pat, html, re.DOTALL)
            if m:
                c2 = clean_html(m.group(1))
                if len(c2) > 40: out["ai_answer"] = c2[:800]; break
        seen = set()
        for m in re.finditer(r'class="[^"]*VwiC3b[^"]*"[^>]*>([\s\S]{40,350}?)</div', html, re.DOTALL):
            t = clean_html(m.group(1))
            if len(t) > 40 and t not in seen:
                seen.add(t); out["snippets"].append(t[:300])
            if len(out["snippets"]) >= 5: break
        out["found"] = bool(out["ai_answer"] or out["snippets"])
    except Exception: pass
    return out

async def web_summarise(query: str, wiki: dict, goog: dict, system_prompt: str, max_tok: int = 500) -> str:
    ctx = []
    if goog["ai_answer"]: ctx.append(f"Google Featured Answer: {goog['ai_answer']}")
    if goog["snippets"]:  ctx.append("Web snippets:\n" + "\n".join(f"- {s}" for s in goog["snippets"]))
    if wiki["found"]:     ctx.append(f"Wikipedia Context ({wiki['title']}):\n{wiki['intro']}")
    if not ctx: return ""
    return await ai(system_prompt, f"User question: {query}\n\nSearch facts & context:\n{chr(10).join(ctx)[:3000]}\n\nAnswer the user directly and concisely based on the above facts.", "", max_tok=max_tok)

# ══════════════════════════════════════════════════════
#  FEATURE SUBSYSTEM: MULTI-FEED RSS NEWS ARCHITECTURE
# ══════════════════════════════════════════════════════
NEWS_FEEDS = {
    "crypto": "https://www.coindesk.com/arc/outboundfeed/rss/",
    "ai": "https://bair.berkeley.edu/blog/feed.xml",
    "tech": "https://techcrunch.com/feed/"
}

def fetch_rss_news(feed_key: str) -> list[dict]:
    url = NEWS_FEEDS.get(feed_key, "https://techcrunch.com/feed/")
    parsed = feedparser.parse(url)
    results = []
    for entry in parsed.entries[:10]:
        title = entry.get("title", "No Title")
        link = entry.get("link", "#")
        summary_raw = entry.get("summary", "")
        soup = BeautifulSoup(summary_raw, "html.parser")
        summary_text = soup.get_text()[:180] + "..."
        img_url = "https://i.postimg.cc/k4kX6bVp/Google-News-icon.png"
        img_match = re.search(r'src=["\'](https://[^"\']+\.(?:jpg|jpeg|png|webp|gif))["\']', summary_raw, re.IGNORECASE)
        if img_match: img_url = img_match.group(1)
        results.append({"title": title, "link": link, "summary": summary_text, "image": img_url})
    return results

async def crypto_news_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await execute_rss_flow(u, c, "crypto", "Crypto Headlines Engine")

async def ai_news_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await execute_rss_flow(u, c, "ai", "AI Artificial Intelligence Feed")

async def tech_news_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await execute_rss_flow(u, c, "tech", "Technology Infrastructure News")

async def execute_rss_flow(u: Update, c: ContextTypes.DEFAULT_TYPE, feed_key: str, full_form_label: str):
    if not u.message: return
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "📰")
    sm = await u.message.reply_text(f"🛰 *Operation: RSS Feed Resolution*\n*Progress: Fetching data from {full_form_label}...*", parse_mode=ParseMode.MARKDOWN)
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, fetch_rss_news, feed_key)
    if not items:
        await sm.edit_text("😿 No operational feed responses recorded. Try again later!", parse_mode=ParseMode.MARKDOWN); return
    await sm.delete()
    top = items[0]
    cap = f"📰 *{full_form_label.upper()}*\n\n📌 *{top['title']}*\n\n{top['summary']}\n\n🔗 [Read Full Story]({top['link']})"
    await u.message.reply_photo(photo=top["image"], caption=cap, parse_mode=ParseMode.MARKDOWN)
    bot_status["message_count"] += 1

# ══════════════════════════════════════════════════════
#  FEATURE SUBSYSTEM: CCXT MARKET DATA & MATPLOTLIB CHARTS
# ══════════════════════════════════════════════════════
async def crypto_price_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    args = c.args or []
    ticker = args[0].upper() if args else "BTC"
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "💰")
    sm = await u.message.reply_text(f"⚡ *Operation: Market Price Retrieval*\n*Progress: Syncing ticker {ticker}/USDT orderbooks...*", parse_mode=ParseMode.MARKDOWN)
    try:
        loop = asyncio.get_running_loop()
        ticker_data = await loop.run_in_executor(None, exchange.fetch_ticker, f"{ticker}/USDT")
        price = ticker_data.get('last', 0.0)
        change = ticker_data.get('percentage', 0.0)
        vol = ticker_data.get('baseVolume', 0.0)
        high = ticker_data.get('high', 0.0)
        low = ticker_data.get('low', 0.0)
        sign = "🟩 +" if change >= 0 else "🟥 "
        res = (
            f"⚡ *MARKET INSTRUMENT REPORT: {ticker}/USDT*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 *Price:* `{price:,.4f} USDT`\n"
            f"📊 *24h Change:* `{sign}{change:.2f}%`\n"
            f"📈 *24h High:* `{high:,.4f}`\n"
            f"📉 *24h Low:* `{low:,.4f}`\n"
            f"🔄 *Volume:* `{vol:,.2f} {ticker}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🐾 _via Beluga Quant Engine_"
        )
        await sm.edit_text(res, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await sm.edit_text(f"😿 *Operation Error:* Unified exchange returned symbol unmapped or unavailable. details: `{str(e)[:60]}`"); bot_status["error_count"] += 1

async def crypto_volume_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    args = c.args or []
    ticker = args[0].upper() if args else "BTC"
    cid = u.effective_chat.id
    sm = await u.message.reply_text(f"⚡ *Operation: Market Volume Resolution*\n*Progress: Parsing trading matrix for {ticker}...*", parse_mode=ParseMode.MARKDOWN)
    try:
        loop = asyncio.get_running_loop()
        ticker_data = await loop.run_in_executor(None, exchange.fetch_ticker, f"{ticker}/USDT")
        vol = ticker_data.get('baseVolume', 0.0)
        quote_vol = ticker_data.get('quoteVolume', 0.0)
        await sm.edit_text(
            f"📊 *VOLUME METRICS: {ticker}/USDT*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 *Base Asset Volume:* `{vol:,.2f} {ticker}`\n"
            f"💵 *Quote Asset Volume:* `{quote_vol:,.2f} USDT`\n━━━━━━━━━━━━━━━━━━━━\n🐾 _Processed smoothly._",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception: await sm.edit_text("😿 Failed to access transactional metrics.")

async def crypto_movers_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    cmd = u.message.text.lower()
    gainers_mode = "topgainers" in cmd
    cid = u.effective_chat.id
    lbl = "Gainers" if gainers_mode else "Losers"
    sm = await u.message.reply_text(f"⚡ *Operation: Volatility Sort Matrix*\n*Progress: Finding top crypto {lbl.lower()}...*", parse_mode=ParseMode.MARKDOWN)
    try:
        loop = asyncio.get_running_loop()
        tickers = await loop.run_in_executor(None, exchange.fetch_tickers)
        records = []
        for sym, t in tickers.items():
            if sym.endswith("/USDT"):
                ch = t.get('percentage', 0.0)
                records.append({"sym": sym.split("/")[0], "ch": ch, "price": t.get('last', 0.0)})
        records.sort(key=lambda x: x["ch"], reverse=gainers_mode)
        lines = [f"📊 *TOP 5 TRADING MARKET {lbl.upper()} (USDT)*\n━━━━━━━━━━━━━━━━━━━━"]
        for i, r in enumerate(records[:5], 1):
            s = "🟩 +" if r["ch"] >= 0 else "🟥 "
            lines.append(f"{i}. *{r['sym']}* • `{r['price']:,.3f}` • `{s}{r['ch']:.2f}%`")
        lines.append("━━━━━━━━━━━━━━━━━━━━\n🐾 _Data updated automatically_")
        await sm.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e: await sm.edit_text(f"😿 System sorting exception: `{str(e)[:50]}`")

async def crypto_chart_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    cmd_text = u.message.text
    parts = cmd_text.split()
    ticker = "BTC"
    timeframe = "1h"
    if len(parts) >= 2: ticker = parts[1].upper()
    cmd_name = parts[0].lower()
    for tf in ["5m", "15m", "1h", "4h", "1d"]:
        if tf in cmd_name: timeframe = tf
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "📈")
    sm = await u.message.reply_text(f"📊 *Operation: Interactive Data Visualization*\n*Progress: Fetching and rendering candlestick values for {ticker} ({timeframe})...*", parse_mode=ParseMode.MARKDOWN)
    try:
        loop = asyncio.get_running_loop()
        symbol = f"{ticker}/USDT"
        ohlcv = await loop.run_in_executor(None, lambda: exchange.fetch_ohlcv(symbol, timeframe, limit=45))
        if not ohlcv: raise ValueError("Empty dataset returned.")
        
        df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
        df.set_index('Timestamp', inplace=True)
        
        # Plot styling parameters execution loop
        buf = io.BytesIO()
        mc = mpf.make_marketcolors(up='#00C48C', down='#ff3366', inherit=True)
        s  = mpf.make_mpf_style(base_mpf_style='charles', marketcolors=mc, gridcolor='#222222', facecolor='#0d0d0d')
        
        def _plot():
            mpf.plot(df, type='candle', style=s, volume=True, savefig=dict(fname=buf, dpi=115, bbox_inches='tight'), figratio=(14,9))
        
        await loop.run_in_executor(None, _plot)
        buf.seek(0)
        await sm.delete()
        await u.message.reply_photo(photo=buf, caption=f"📊 *{ticker}/USDT* • `{timeframe}` Chart Layout\n🐾 _Rendered instantly via Beluga Graphics._", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await sm.edit_text(f"😿 *Operation Error:* Visualization compilation broken: `{str(e)[:60]}`")

# ══════════════════════════════════════════════════════
#  FEATURE SUBSYSTEM: QR CODE GENERATION & DESERIALIZATION
# ══════════════════════════════════════════════════════
async def qr_generate_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("🐱 Usage: `/qr numerical or textual content here`")
        return
    payload = parts[1].strip()
    cid = u.effective_chat.id
    sm = await u.message.reply_text("🟩 *Operation: Matrix Transformation*\n*Progress: Translating data strings to QR elements...*", parse_mode=ParseMode.MARKDOWN)
    try:
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
        await u.message.reply_photo(photo=bio, caption="🤖 *Matrix QR Code Configuration Successfully Dispatched.*\n🐾 _Generated by Beluga Tools._")
    except Exception as e: await sm.edit_text(f"😿 QR pipeline fault: `{str(e)}`")

async def qr_scan_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Target parameter missing! Please reply to an image contains a QR code with `/scanqr`.")
        return
    cid = u.effective_chat.id
    sm = await u.message.reply_text("🟩 *Operation: Computer Vision Matrix Sweep*\n*Progress: Decoding structural components...*", parse_mode=ParseMode.MARKDOWN)
    try:
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
            val, points, _ = detector.detectAndDecode(img)
            return val
        decoded_text = await loop.run_in_executor(None, _decode)
        if decoded_text:
            await sm.edit_text(f"🤖 *COMPUTER VISION DETECTOR SCAN SUCCESSFUL*\n━━━━━━━━━━━━━━━━━━━━\n📝 *Decoded Payload:*\n```\n{decoded_text}\n```\n━━━━━━━━━━━━━━━━━━━━", parse_mode=ParseMode.MARKDOWN)
        else:
            await sm.edit_text("😿 *Computer Vision Report:* Optical matrix data pattern unreadable. Ensure high contrast.")
    except Exception as e: await sm.edit_text(f"😿 Analysis aborted: `{str(e)[:60]}`")

# ══════════════════════════════════════════════════════
#  FEATURE SUBSYSTEM: DIGITAL IMAGE MANIPULATION TOOLS
# ══════════════════════════════════════════════════════
async def img_info_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Target missing. Reply to a compressed photo asset.")
        return
    sm = await u.message.reply_text("📦 *Operation: Structural Parsing*\n*Progress: Reading media headers...*", parse_mode=ParseMode.MARKDOWN)
    try:
        p = u.message.reply_to_message.photo[-1]
        f = await c.bot.get_file(p.file_id)
        b = io.BytesIO(); await f.download_to_memory(b); b.seek(0)
        im = Image.open(b)
        await sm.edit_text(
            f"🖼 *OPERATIONAL IMAGE PROPERTY REPORT*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📐 *Resolution Bounds:* `{im.size[0]} x {im.size[1]} pixels`\n"
            f"🎨 *Color Spectrum Profile:* `{im.mode}`\n"
            f"💾 *Transmitted Size:* `{p.file_size / 1024:.2f} KB`\n"
            f"🧱 *Underlying Format:* `{im.format}`\n━━━━━━━━━━━━━━━━━━━━ Profile scanned.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception: await sm.edit_text("😿 Resource parsing failure.")

async def img_resize_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Target missing. Reply to a picture asset with `/resize`.")
        return
    sm = await u.message.reply_text("📦 *Operation: Spatial Scale Mutation*\n*Progress: Transforming matrix down to 512x512 standard pipeline...*", parse_mode=ParseMode.MARKDOWN)
    try:
        p = u.message.reply_to_message.photo[-1]
        f = await c.bot.get_file(p.file_id)
        b = io.BytesIO(); await f.download_to_memory(b); b.seek(0)
        loop = asyncio.get_running_loop()
        def _scale():
            im = Image.open(b)
            out = im.resize((512, 512), Image.Resampling.LANCZOS)
            out_b = io.BytesIO(); out.save(out_b, "PNG"); out_b.seek(0)
            return out_b
        res_b = await loop.run_in_executor(None, _scale)
        await sm.delete()
        await u.message.reply_photo(photo=res_b, caption="📐 *Matrix Remapped to 512x512 Uniform Resolution Bounds.*")
    except Exception: await sm.edit_text("😿 Resizing pipeline error.")

async def img_compress_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Target missing. Reply to a high resolution picture asset.")
        return
    sm = await u.message.reply_text("📦 *Operation: Bitrate Quantization*\n*Progress: Truncating quantization tables...*", parse_mode=ParseMode.MARKDOWN)
    try:
        p = u.message.reply_to_message.photo[-1]
        f = await c.bot.get_file(p.file_id)
        b = io.BytesIO(); await f.download_to_memory(b); b.seek(0)
        loop = asyncio.get_running_loop()
        def _crunch():
            im = Image.open(b)
            out_b = io.BytesIO()
            im.save(out_b, "JPEG", quality=22)
            out_b.seek(0)
            return out_b
        res_b = await loop.run_in_executor(None, _crunch)
        await sm.delete()
        await u.message.reply_photo(photo=res_b, caption="💾 *Bitrate Quantization Completed. Low-memory alternative compiled successfully.*")
    except Exception: await sm.edit_text("😿 Compression tables execution fault.")

async def img_watermark_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text("🐱 Target missing. Reply to an image using `/watermark your text`.")
        return
    parts = u.message.text.split(maxsplit=1)
    wm_text = parts[1].strip() if len(parts) > 1 else "TEAM OLDY CRYPTO"
    sm = await u.message.reply_text("📦 *Operation: Visual Identity Blending*\n*Progress: Overlaying canvas parameters...*", parse_mode=ParseMode.MARKDOWN)
    try:
        p = u.message.reply_to_message.photo[-1]
        f = await c.bot.get_file(p.file_id)
        b = io.BytesIO(); await f.download_to_memory(b); b.seek(0)
        loop = asyncio.get_running_loop()
        def _inject():
            im = Image.open(b).convert("RGBA")
            txt_layer = Image.評RGBA = Image.new("RGBA", im.size, (255,255,255,0))
            draw = ImageDraw.Draw(txt_layer)
            # Center coordinates configuration mapping logic loop
            x = im.size[0] // 2 - 100
            y = im.size[1] - 50
            draw.text((x, y), wm_text, fill=(255, 196, 140, 160))
            combined = Image.alpha_composite(im, txt_layer)
            out_b = io.BytesIO()
            combined.convert("RGB").save(out_b, "JPEG")
            out_b.seek(0)
            return out_b
        res_b = await loop.run_in_executor(None, _inject)
        await sm.delete()
        await u.message.reply_photo(photo=res_b, caption="🛡 *Identity Signature Applied to Graphic Asset Bounds.*")
    except Exception as e: await sm.edit_text(f"😿 Watermark process crashed: `{str(e)}`")

# ══════════════════════════════════════════════════════
#  FEATURE SUBSYSTEM: COMPREHENSIVE BOT STATISTICS MODULE
# ══════════════════════════════════════════════════════
async def bot_stats_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    up = (datetime.now() - bot_status["start_time"]).total_seconds()
    loop = asyncio.get_running_loop()
    
    def _crunch():
        # Aggregating metrics safely using pandas & numpy structures
        metrics = [
            bot_status["message_count"],
            bot_status["error_count"],
            bot_status["api_calls"],
            bot_status["failed_apis"]
        ]
        arr = np.array(metrics)
        df = pd.DataFrame(arr, index=["Messages Handled", "Errors Tracked", "API Hits", "API Failures"], columns=["Metric Count"])
        return df.to_string()
        
    res_df_str = await loop.run_in_executor(None, _crunch)
    text = (
        f"🤖 *BELUGA HIGH-PERFORMANCE INFRASTRUCTURE METRICS*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ *System Uptime:* `{up / 3600:.2f} Hours`\n"
        f"📊 *Runtime Array State:*\n```\n{res_df_str}\n```\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🐾 _Everything running perfectly._"
    )
    await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════════════
#  FEATURE SUBSYSTEM: MATHEMATICAL DATE UTILITIES
# ══════════════════════════════════════════════════════
async def date_utils_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split()
    if len(parts) < 2:
        await u.message.reply_text("🐱 Usage: `/countdown DD-MM-YYYY` or `/daysleft DD-MM-YYYY`")
        return
    raw_date = parts[1].strip()
    try:
        target = datetime.strptime(raw_date, "%d-%m-%m" if "-" not in raw_date else "%d-%m-%Y")
        now = datetime.now()
        diff = target - now
        days = diff.days
        if days < 0:
            await u.message.reply_text("😿 That date has already passed, silly! Time moves forward, meow! 🐾")
            return
        
        cmd = parts[0].lower()
        if "countdown" in cmd:
            hours, remainder = divmod(diff.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            await u.message.reply_text(f"⏳ *TEMPORAL COUNTDOWN MATRIX*\n━━━━━━━━━━━━━━━━━━━━\n🎯 *Target:* `{raw_date}`\n⏱ *Remaining:* `{days} days, {hours} hours, and {minutes} minutes!`\n━━━━━━━━━━━━━━━━━━━━\n🐾 _Calculated accurately._", parse_mode=ParseMode.MARKDOWN)
        else:
            await u.message.reply_text(f"📅 *Operational Days Counter:* Exactly `{days + 1}` days remaining until `{raw_date}` threshold target.")
    except Exception:
        await u.message.reply_text("😿 Format verification rejected. Use explicit format layout: `DD-MM-YYYY` (Example: `25-12-2026`)")

# ══════════════════════════════════════════════════════
#  FEATURE SUBSYSTEM: BINARY PROCESSING SYSTEMS
# ══════════════════════════════════════════════════════
async def binary_encode_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("🐱 Usage: `/encode structural text block`")
        return
    payload = parts[1].strip()
    try:
        bin_str = " ".join(f"{ord(char):08b}" for char in payload)
        await u.message.reply_text(f"`{bin_str}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e: await u.message.reply_text(f"😿 Processing glitch: {str(e)}")

async def binary_decode_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("🐱 Usage: `/decode 01101000 01101001`")
        return
    raw_bin = parts[1].strip()
    
    # Attempt immediate operational cleanup of command message to preserve privacy architecture
    try: await u.message.delete()
    except Exception: pass
    
    # Validation engine check
    if not re.match(r'^[01\s]+$', raw_bin):
        await c.bot.send_message(chat_id=u.effective_chat.id, text="😿 *Input Validation Refused:* Data stream components contain characters that violate explicit base-2 structural framing constraints.")
        return
    try:
        blocks = raw_bin.split()
        decoded_chars = [chr(int(b, 2)) for b in blocks if len(b) <= 8]
        result_text = "".join(decoded_chars)
        await c.bot.send_message(chat_id=u.effective_chat.id, text=result_text)
    except Exception:
        await c.bot.send_message(chat_id=u.effective_chat.id, text="😿 *Transformation Error:* Data stream structural alignment broken during deserialization conversion loops.")

# ══════════════════════════════════════════════════════
#  QUIZ LOGIC CORE MODULE
# ══════════════════════════════════════════════════════
QUIZ_TOPICS = ["deep ocean biology","quantum mechanics","human brain","solar system","animal behaviour","black holes","DNA genetics","ancient Egypt","World War 2"]
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
            raw = await ai("Trivia master. Output ONLY raw JSON.", f"Topic: '{topic}'. Generate 1 MC question.\n" '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}', "", max_tok=200)
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
        except Exception: pass
    return None

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        parts = u.message.text.split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 and parts[1].strip() else random.choice(QUIZ_TOPICS)
        cid = str(u.effective_chat.id); cid_i = u.effective_chat.id
        await safe_react(c.bot, cid_i, u.message.message_id, "💡")
        await c.bot.send_chat_action(cid_i, "typing")
        sm = await u.message.reply_text("🎲 *Operation: Quiz Generation*\n*Progress: Building logical prompt constraints...*", parse_mode=ParseMode.MARKDOWN)
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
            except Exception: pass
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
        await update_score(cid, uid, name, +10)
    except Exception: pass

# ══════════════════════════════════════════════════════
#  LEADERBOARD ARCHITECTURE
# ══════════════════════════════════════════════════════
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid = str(u.effective_chat.id)
        await check_and_load_group(cid)
        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)
        seen_ids = set()
        clean_lb = []
        for entry in lb:
            if entry.get("user_id") not in seen_ids:
                seen_ids.add(entry.get("user_id")); clean_lb.append(entry)
        lw = db.get("weekly", {}).get(cid, {})
        lines = []
        if lw and lw.get("top3"):
            lines.append("🏆 LAST WEEK CHAMPIONS 🏆\n")
            for i, e in enumerate(lw["top3"]): lines.append(f"{MEDALS[i]} {e.get('name','?')[:18]} — {e.get('score',0):,} pts")
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n")
        lines += ["╔════════════════════════════╗", "🏆  CURRENT LEADERBOARD  🏆", "╚════════════════════════════╝\n"]
        if not clean_lb: lines.append("No scores yet!")
        else:
            for i, e in enumerate(clean_lb[:10]):
                m = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
                lines.append(f"{m} {e.get('name','Unknown')[:18]:<18} {e.get('score',0):>6,} pts")
        lines += ["\n━━━━━━━━━━━━━━━━━━━━", "➕ +10 quiz/ttt · +700 mine  ➖ -10 loss  ⭐ +50 GM"]
        text = "\n".join(lines)
        try: await u.message.reply_photo(photo=LB_IMAGE_URL, caption=text, parse_mode=ParseMode.MARKDOWN)
        except Exception: await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[lb] {e}", exc_info=True)

async def nw_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only."); return
        cid = str(u.effective_chat.id); await check_and_load_group(cid)
        lb = sorted(db.get("scores",{}).get(cid,{}).values(), key=lambda x: x.get("score",0), reverse=True)
        seen_ids = set(); clean_lb = []
        for entry in lb:
            if entry.get("user_id") not in seen_ids: seen_ids.add(entry.get("user_id")); clean_lb.append(entry)
        top3 = [{"name": e.get("name","?"), "score": e.get("score",0)} for e in clean_lb[:3]]
        wk_label = datetime.now().strftime("%d %b %Y")
        db.setdefault("weekly",{})[cid] = {"top3": top3, "week_label": wk_label}
        db["scores"][cid] = {}
        db_needs_sync_groups.add(cid)
        announce = ["🏆🎉 *NEW WEEK!* 🎉🏆", f"\n_Week: {wk_label}_\n", "👑 *Champions:*\n"]
        for i, e in enumerate(top3): announce.append(f"{MEDALS[i]} *{e['name']}* — {e['score']:,} pts")
        announce += ["\n🔄 *All scores reset!*", "🚀 _New battle begins!_"]
        await u.message.reply_text("\n".join(announce), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[nw] {e}")

async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only."); return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("⚠️ Reply to a user."); return
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await u.message.reply_text("⚠️ Usage: `/pump 100`", parse_mode=ParseMode.MARKDOWN); return
        amount = int(parts[1])
        cmd = parts[0].lstrip("/").lower().split("@")[0]
        delta = +amount if cmd == "pump" else -amount
        target = u.message.reply_to_message.from_user; cid = str(u.effective_chat.id)
        new_sc = await update_score(cid, str(target.id), (target.first_name or "User")[:30], delta)
        emoji = "🚀" if cmd == "pump" else "📉"; sign = "+" if delta > 0 else ""
        await u.message.reply_text(f"{emoji} *{'PUMP' if cmd=='pump' else 'DUMP'}*\n\n" f"👤 *{target.first_name}*\n" f"{'📈' if delta>0 else '📉'} {sign}{amount:,} pts\n" f"💰 New total: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[pump_dump] {e}")

# ══════════════════════════════════════════════════════
#  MINESWEEPER INTERACTIVE GAME MODULE
# ══════════════════════════════════════════════════════
def _mine_setup_keyboard(gkey: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("3 Mines", callback_data=f"mine:set:{gkey}:3"),
        InlineKeyboardButton("4 Mines", callback_data=f"mine:set:{gkey}:4"),
        InlineKeyboardButton("5 Mines", callback_data=f"mine:set:{gkey}:5"),
    ]])

def _mine_board_keyboard(gkey: str, state: list, revealed: list, disabled: bool = False) -> InlineKeyboardMarkup:
    rows, r = [], []
    for i in range(6):
        if disabled or revealed[i]:
            label = "💣" if state[i] else ("✅" if revealed[i] else "⬜")
            btn = InlineKeyboardButton(label, callback_data=f"mine:noop:{gkey}:{i}")
        else: btn = InlineKeyboardButton("📦", callback_data=f"mine:play:{gkey}:{i}")
        r.append(btn)
        if len(r) == 3: rows.append(r); r = []
    if r: rows.append(r)
    return InlineKeyboardMarkup(rows)

def mine_build_text(g: dict, rem: int) -> str:
    bombs = g["bombs"]; opened_count = sum(1 for x in g["revealed"] if x); total_safe = 6 - bombs; status = g.get("status", "playing")
    if status == "timeout": return "⏰ *Time Up!*\n\nYou took too long. Lost *-5 pts*."
    elif status == "lost": return "💥 *BOOM!* You hit a mine!\n\nLost *-5 pts*."
    elif status == "won": return f"🎉 *YOU WIN!*\n\nAll {total_safe} safe boxes found! Won *+700 pts*."
    else: return f"💣 *MINESWEEPER*\n\nFind all safe boxes! Avoid the mines.\nMines: {bombs}  |  Safe found: {opened_count}/{total_safe}\n⏱ Time left: `{rem}s`"

async def mine_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        cid = str(u.effective_chat.id); uid = str(u.effective_user.id)
        now = time.time(); m_stat = mine_play_stats.setdefault(uid, {"plays": 0, "block_until": 0})
        if now < m_stat["block_until"]:
            rem_m = max(1, int((m_stat["block_until"] - now) // 60))
            await u.message.reply_text(f"⏳ *Cooldown Active!*\n\nYou've played too much! Let your paws rest. 🐾\nYou can play Minesweeper again in {rem_m} minutes.", parse_mode=ParseMode.MARKDOWN); return
        m_stat["plays"] += 1
        if m_stat["plays"] > 20:
            m_stat["block_until"] = now + 3600; m_stat["plays"] = 0
            await u.message.reply_text("🛑 *Minesweeper Limit Reached!*\n\nYou've hit the limit of 20 games! Taking a mandatory 1-hour break to recalibrate! 💥", parse_mode=ParseMode.MARKDOWN); return
        gkey = f"{cid}_{uid}_{int(now)}"
        mine_games[gkey] = {"uid": uid, "name": (u.effective_user.first_name or "Player")[:20], "bombs": 0, "state": [], "revealed": [False]*6, "chat_id": u.effective_chat.id, "msg_id": None, "status": "setting"}
        msg = await u.message.reply_photo(photo=MINE_IMAGE_URL, caption="💣 *MINESWEEPER*\n\nChoose number of mines:", reply_markup=_mine_setup_keyboard(gkey), parse_mode=ParseMode.MARKDOWN)
        mine_games[gkey]["msg_id"] = msg.message_id; bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[mine] {e}")

async def run_mine_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    try:
        while True:
            await asyncio.sleep(5); g = mine_games.get(gkey); td = mine_timers.get(gkey)
            if not g or not td or g.get("status") != "playing": return
            td["remaining"] = max(0, td["remaining"] - 5); cid = g.get("chat_id"); msg_id = g.get("msg_id")
            if not msg_id: return
            if td["remaining"] <= 0:
                g["status"] = "timeout"; new_sc = await update_score(str(cid), g["uid"], g["name"], -5)
                try: await c.bot.edit_message_caption(chat_id=cid, message_id=msg_id, caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True))
                except Exception: pass
                mine_timers.pop(gkey, None); mine_games.pop(gkey, None); return
            else:
                try: await c.bot.edit_message_caption(chat_id=cid, message_id=msg_id, caption=mine_build_text(g, td["remaining"]), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"]))
                except BadRequest as e:
                    if "not modified" not in str(e).lower(): logger.debug(f"[Mine Timer Edit] {e}")
                except Exception: pass
    except asyncio.CancelledError: pass

async def mine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        try: await q.answer()
        except Exception: pass
        parts = q.data.split(":"); action, gkey, val = parts[1], parts[2], int(parts[3])
        if gkey not in mine_games: return
        g = mine_games[gkey]
        if str(q.from_user.id) != g["uid"]: await q.answer("This game isn't yours! 😅", show_alert=True); return
        if action == "noop": return
        if action == "set":
            if g.get("status") != "setting": return
            bombs = max(3, min(5, val)); state = [True]*bombs + [False]*(6-bombs); random.shuffle(state)
            g.update({"bombs": bombs, "state": state, "status": "playing", "revealed": [False]*6})
            mine_timers[gkey] = {"remaining": 60}; asyncio.create_task(run_mine_timer(context, gkey))
            try: await q.edit_message_caption(caption=mine_build_text(g, 60), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, state, g["revealed"]))
            except Exception: pass
        elif action == "play":
            if g.get("status") != "playing" or g["revealed"][val]: return
            state = g["state"]; is_bomb = state[val]; cid = str(q.message.chat_id)
            if is_bomb:
                g["status"] = "lost"; mine_timers.pop(gkey, None); new_sc = await update_score(cid, g["uid"], g["name"], -5)
                try: await q.edit_message_caption(caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, state, g["revealed"], disabled=True))
                except Exception: pass
                mine_games.pop(gkey, None)
            else:
                g["revealed"][val] = True; total_safe = 6 - g["bombs"]; opened_count = sum(1 for x in g["revealed"] if x)
                if gkey in mine_timers: mine_timers[gkey]["remaining"] = 60
                if opened_count >= total_safe:
                    g["status"] = "won"; mine_timers.pop(gkey, None); new_sc = await update_score(cid, g["uid"], g["name"], +700)
                    try: await q.edit_message_caption(caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, state, g["revealed"], disabled=True))
                    except Exception: pass
                    mine_games.pop(gkey, None)
                else:
                    rem = mine_timers.get(gkey, {}).get("remaining", 60)
                    try: await q.edit_message_caption(caption=mine_build_text(g, rem), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, state, g["revealed"]))
                    except Exception: pass
    except Exception as e: logger.error(f"[mine_callback] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  DAILY CHECK-IN ATTENDANCE ENGINE
# ══════════════════════════════════════════════════════
def _build_gm_caption(users: list, date_str: str) -> str:
    lines = ["📸 *DAILY ATTENDANCE*\n", "🥱 Mark your attendance below!\n", f"📅 {date_str}  |  👥 Present: {len(users)}\n", "━━━━━━━━━━━━━━━━━━━━\n"]
    display_users = users[-15:] if len(users) > 15 else users
    if len(users) > 15: lines.append(f"... and {len(users) - 15} more...\n")
    for i, user in enumerate(display_users, 1): lines.append(f"{i}. {user['name']} • {user['time']}")
    lines += ["\n━━━━━━━━━━━━━━━━━━━━\n", "🔥 Daily check-in = *+50 pts*", "👇 Press GM to mark attendance!"]
    return "\n".join(lines)

def _build_gm_keyboard(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}")]])

async def gm_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text("🚫 Owner only."); return
        cid = str(u.effective_chat.id); date_str = datetime.now().strftime("%d %b %Y")
        msg = None
        try: msg = await u.message.reply_photo(photo=GM_IMAGE_URL, caption=_build_gm_caption([], date_str), reply_markup=_build_gm_keyboard(cid), parse_mode=ParseMode.MARKDOWN)
        except Exception:
            try: msg = await u.message.reply_text(text=_build_gm_caption([], date_str), reply_markup=_build_gm_keyboard(cid), parse_mode=ParseMode.MARKDOWN)
            except Exception: return
        gm_tracker[cid] = (msg.message_id, [], date_str); gm_msg_lock[cid] = asyncio.Lock(); bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[gm] {e}")

async def gm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        parts = q.data.split(":"); cid = parts[2]
        if cid not in gm_msg_lock: gm_msg_lock[cid] = asyncio.Lock()
        async with gm_msg_lock[cid]:
            if cid not in gm_tracker: await q.answer("⏰ Attendance session expired", show_alert=True); return
            msg_id, users, date_str = gm_tracker[cid]; user = q.from_user; user_id = str(user.id); utime = datetime.now().strftime("%H:%M")
            already = next((uu for uu in users if uu.get("id") == user_id), None)
            if already: await q.answer(f"✅ Already marked at {already['time']}", show_alert=True); return
            u_name = (user.first_name or "User")[:20]
            users.append({"id": user_id, "name": u_name, "time": utime})
            gm_tracker[cid] = (msg_id, users, date_str)
            await update_score(str(q.message.chat_id), user_id, u_name, +50)
            try:
                new_cap = _build_gm_caption(users, date_str)
                if q.message.photo: await context.bot.edit_message_caption(chat_id=q.message.chat_id, message_id=msg_id, caption=new_cap, reply_markup=_build_gm_keyboard(cid), parse_mode=ParseMode.MARKDOWN)
                else: await context.bot.edit_message_text(chat_id=q.message.chat_id, message_id=msg_id, text=new_cap, reply_markup=_build_gm_keyboard(cid), parse_mode=ParseMode.MARKDOWN)
                await q.answer(f"✅ Attendance marked for {u_name}! +50 pts", show_alert=False)
            except Exception: await q.answer("✅ Marked!", show_alert=False)
    except Exception as e: logger.error(f"[gm_callback] {e}")

# ══════════════════════════════════════════════════════
#  PERSISTENT FUN DISPATCHER MECHANISMS (GITHUB HOSTED)
# ══════════════════════════════════════════════════════
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global fun_db_needs_sync
    if not u.message: return
    try:
        cid = str(u.effective_chat.id); await check_and_load_group(cid); await check_and_load_fun_db()
        cmd = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        
        # Pull members verified through standard chat activity trackers
        active_users = list(db.get("seen", {}).get(cid, {}).values())
        if len(active_users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text("😿 Need more active group members to process fun parameters first!"); return
            
        day = datetime.now().strftime("%y-%m-%d"); lk = f"{cid}:{cmd}"
        locks = db.setdefault("locks", {}).setdefault(cid, {})
        
        if lk in locks and locks[lk]["date"] == day: res = locks[lk]["res"]
        else:
            if cmd == "couple":
                m = random.sample(active_users, 2)
                res = f"💖 *{m[0]['n']}* 💞 *{m[1]['n']}*\n100% compatible today!"
                # Persistent retention sync tracking log injection loops
                for person in m:
                    fun_db["users"][str(person["id"])] = {"name": person["n"], "last_matched": day}
            else:
                m = [random.choice(active_users)]
                res = f"🌈 *{m[0]['n']}* is today's certified special rainbow! 🌈"
                fun_db["users"][str(m[0]["id"])] = {"name": m[0]["n"], "rainbow_date": day}
                
            locks[lk] = {"date": day, "res": res}
            db_needs_sync_groups.add(cid)
            fun_db_needs_sync = True
            
        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[fun] {e}")

# ══════════════════════════════════════════════════════
#  TIC TAC TOE PROTOCOL MANAGEMENT ENGINE
# ══════════════════════════════════════════════════════
def register_player(uid: str, gkey: str): user_in_game[uid] = gkey
def release_player(uid: str): user_in_game.pop(uid, None)
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

async def run_game_timer(c: ContextTypes.DEFAULT_TYPE, gkey: str):
    try:
        while True:
            await asyncio.sleep(5); g = ttt_games.get(gkey); td = game_timers.get(gkey)
            if not g or not td or g.get("status") != "playing": return
            td["remaining"] = max(0, td["remaining"] - 5); cid, msg_id = g.get("chat_id"), g.get("msg_id")
            if not msg_id: return
            if td["remaining"] <= 0:
                loser_uid, loser_name = (str(g["x_id"]), g["x_name"]) if g["turn"] == "X" else (str(g["o_id"]), g["o_name"])
                winner_uid, winner_name = (str(g["o_id"]), g["o_name"]) if g["turn"] == "X" else (str(g["x_id"]), g["x_name"])
                g["status"] = "timeout"; g["winner_name"] = winner_name
                try: await c.bot.edit_message_text(chat_id=cid, message_id=msg_id, text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"], disabled=True))
                except Exception: pass
                if not g["vs_bot"]:
                    await update_score(str(cid), winner_uid, winner_name, +10); await update_score(str(cid), loser_uid, loser_name, -10)
                release_player(str(g["x_id"])); release_player(str(g["o_id"]))
                game_timers.pop(gkey, None); ttt_games.pop(gkey, None); return
            else:
                try: await c.bot.edit_message_text(chat_id=cid, message_id=msg_id, text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"]))
                except Exception: pass
    except asyncio.CancelledError: pass

TTT_EMPTY = "⬜"; TTT_X = "❌"; TTT_O = "⭕"
WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

def ttt_check_winner(board: list) -> Optional[str]:
    for a,b,cc in WINS:
        if board[a] == board[b] == board[cc] and board[a] != TTT_EMPTY: return board[a]
    return None

def ttt_is_draw(board: list) -> bool: return all(c != TTT_EMPTY for c in board) and not ttt_check_winner(board)

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
        if is_max: best = max(best, score); alpha = max(alpha, best)
        else: best = min(best, score); beta = min(beta, best)
        if beta <= alpha: break
    return best

def ttt_bot_move(board: list) -> int:
    best_score = -1000; best_move = -1
    for i in range(9):
        if board[i] != TTT_EMPTY: continue
        board[i] = TTT_O; score = _minimax(board, False, -1000, 1000); board[i] = TTT_EMPTY
        if score > best_score: best_score = score; best_move = i
    return best_move

def ttt_build_keyboard(board: list, disabled: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx = row*3 + col; cell = board[idx]
            cb = f"ttt:noop:{idx}" if (cell != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            r.append(InlineKeyboardButton(cell, callback_data=cb))
        rows.append(r)
    return InlineKeyboardMarkup(rows)

def ttt_build_text(g: dict) -> str:
    x_name = g["x_name"]; o_name = g["o_name"]; turn = g["turn"]; status = g.get("status","playing")
    gkey = f"{g['chat_id']}:{g.get('msg_id','')}"; td = game_timers.get(gkey, {}); rem = td.get("remaining", TIMER_DURATION)
    tsec = f"{rem//60:02d}:{rem%60:02d}"; board = g["board"]
    board_str = "\n".join([" ".join(board[r*3+col] for col in range(3)) for r in range(3)])
    if status == "playing": sl = f"🎯 *{x_name if turn == 'X' else o_name}'s turn* {'❌' if turn == 'X' else '⭕'}  ⏱ `{tsec}`"
    elif status == "timeout": sl = f"⏰ *Time up!*\n🏆 *{g.get('winner_name','')}* wins! +10 pts\n📉 *{g['x_name'] if g['turn'] == 'X' else g['o_name']}* -10 pts"
    elif status == "draw": sl = "🤝 *Draw!* No points awarded."
    else: sl = f"🏆 *{g.get('winner_name','')}* wins! +10 pts\n📉 *{g['o_name'] if g.get('winner_name') == g['x_name'] else g['x_name']}* -10 pts"
    return f"🎮 *TIC TAC TOE*\n━━━━━━━━━━━━━━\n❌ {x_name}  🆚  ⭕ {o_name}\n━━━━━━━━━━━━━━\n\n{board_str}\n\n━━━━━━━━━━━━━━\n{sl}"

def _ready_keyboard(gkey: str) -> InlineKeyboardMarkup: return InlineKeyboardMarkup([[InlineKeyboardButton("READY 🔥", callback_data=f"ttt_ready:{gkey}")]])
def _ready_text(g: dict) -> str: return f"🎮 *TIC TAC TOE — LOBBY*\n━━━━━━━━━━━━━━━━━━━━\n\n❌ {g['x_name']}: {'✅ READY' if g.get('x_ready') else '⏳ Waiting'}\n⭕ {g['o_name']}: {'✅ READY' if g.get('o_ready') else '⏳ Waiting'}\n\n━━━━━━━━━━━━━━━━━━━━\n_Both press READY to start!_ ⚔️"

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    try:
        await cleanup_expired_games(); ua = u.effective_user; cid = u.effective_chat.id; uid_a = str(ua.id); name_a = (ua.first_name or "Player")[:20]
        vs_bot, user_b_id, name_b = True, None, "🤖 Bot"
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot, user_b_id, name_b = False, rb.id, (rb.first_name or "Player2")[:20]
                if player_busy(str(rb.id)): await u.message.reply_text("⚠️ That player is already in a game!"); return
        if player_busy(uid_a): await u.message.reply_text("⚠️ You're already in a game!"); return
        board = [TTT_EMPTY] * 9
        g = {"board": board, "turn": "X", "x_id": ua.id, "x_name": name_a, "o_id": user_b_id if not vs_bot else -1, "o_name": name_b, "vs_bot": vs_bot, "status": "waiting" if not vs_bot else "playing", "created": time.time(), "chat_id": cid, "msg_id": None, "x_ready": False, "o_ready": False}
        if vs_bot:
            msg = await u.message.reply_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
            g.update({"status": "playing", "msg_id": msg.message_id}); gkey = game_key(msg.message_id, cid); ttt_games[gkey] = g
            game_timers[gkey] = {"remaining": TIMER_DURATION}; register_player(uid_a, gkey); asyncio.create_task(run_game_timer(c, gkey))
        else:
            msg = await u.message.reply_text(_ready_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=_ready_keyboard(f"temp_{int(time.time())}"))
            g["msg_id"] = msg.message_id; gkey = game_key(msg.message_id, cid); ttt_games[gkey] = g
            register_player(uid_a, gkey); register_player(str(user_b_id), gkey)
        bot_status["message_count"] += 1
    except Exception as e: logger.error(f"[tictac] {e}", exc_info=True)

async def ttt_ready_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        try: await q.answer()
        except Exception: pass
        cid, mid = q.message.chat_id, q.message.message_id; gkey = game_key(mid, cid); g = ttt_games.get(gkey)
        if not g or g.get("status") != "waiting": return
        uid = str(q.from_user.id)
        if uid == str(g["x_id"]): g["x_ready"] = True
        elif uid == str(g["o_id"]): g["o_ready"] = True
        else: await q.answer("❌ You're not in this game!", show_alert=True); return
        if g["x_ready"] and g["o_ready"]:
            g["status"] = "playing"; game_timers[gkey] = {"remaining": TIMER_DURATION}
            try: await q.edit_message_text(text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"]))
            except Exception: pass
            asyncio.create_task(run_game_timer(context, gkey))
        else:
            try: await q.edit_message_text(text=_ready_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=_ready_keyboard(gkey))
            except Exception: pass
    except Exception as e: logger.error(f"[ttt_ready] {e}")

async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    try:
        try: await q.answer()
        except Exception: pass
        parts = q.data.split(":"); action, idx = parts[1], int(parts[2]); cid, mid = q.message.chat_id, q.message.message_id; gkey = game_key(mid, cid); g = ttt_games.get(gkey)
        if not g or g["status"] != "playing" or action == "noop": return
        uid = str(q.from_user.id); is_part = uid in [str(g["x_id"]), str(g["o_id"])]
        if g["turn"] == "X" and uid != str(g["x_id"]):
            if is_part: await q.answer("Not your turn!", show_alert=True)
            return
        if g["turn"] == "O" and not g["vs_bot"] and uid != str(g["o_id"]):
            if is_part: await q.answer("Not your turn!", show_alert=True)
            return
        board = g["board"]
        if board[idx] != TTT_EMPTY: return
        if gkey in game_timers: game_timers[gkey]["remaining"] = TIMER_DURATION
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O; ws = ttt_check_winner(board)

        async def _end_game(winner_sym=None):
            if winner_sym:
                g["status"], g["winner_name"] = "win", (g["x_name"] if winner_sym == TTT_X else g["o_name"])
                if not g["vs_bot"]:
                    await update_score(str(cid), str(g["x_id"] if winner_sym == TTT_X else g["o_id"]), g["winner_name"], +10)
                    await update_score(str(cid), str(g["o_id"] if winner_sym == TTT_X else g["x_id"]), g["o_name"] if winner_sym == TTT_X else g["x_name"], -10)
                elif winner_sym == TTT_X: await update_score(str(cid), str(g["x_id"]), g["x_name"], +10)
            else: g["status"] = "draw"
            game_timers.pop(gkey, None); release_player(str(g["x_id"])); release_player(str(g["o_id"])); ttt_games.pop(gkey, None)

        if ws:
            await _end_game(ws)
            try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except Exception: pass
            return
        if ttt_is_draw(board):
            await _end_game()
            try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except Exception: pass
            return
        g["turn"] = "O" if g["turn"] == "X" else "X"
        if g["vs_bot"] and g["turn"] == "O":
            bi = ttt_bot_move(board)
            if bi >= 0:
                board[bi] = TTT_O; ws2 = ttt_check_winner(board)
                if ws2:
                    await _end_game(ws2)
                    try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
                    except Exception: pass
                    return
                if ttt_is_draw(board):
                    await _end_game()
                    try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
                    except Exception: pass
                    return
                g["turn"] = "X"
        try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        except Exception: pass
    except Exception as e: logger.error(f"[ttt_cb] {e}", exc_info=True)

# ══════════════════════════════════════════════════════
#  MIDDLEWARE AND LINGUISTIC NLP PROCESSING OVERLAYS
# ══════════════════════════════════════════════════════
def process_linguistic_sentiment_analysis(text_content: str) -> str:
    """
    Executes real-time structural analysis of incoming context fields to adapt persona tones.
    """
    try:
        detected_lang = detect(text_content)
    except Exception: detected_lang = "en"
    
    try:
        blob = TextBlob(text_content)
        polarity = blob.sentiment.polarity
    except Exception: polarity = 0.0
    
    # Selection algorithm for adaptive dynamic persona framing
    if polarity > 0.35: mood_modifier = " Be exceptionally cheerful, friendly, flirty, and warm."
    elif polarity < -0.35: mood_modifier = " Be deeply empathetic, supportive, sweet, and comforting."
    else: mood_modifier = ""
    
    return f"{CHAT_PROMPT}\n- Context Language Target: Detected code `{detected_lang}`.{mood_modifier}"

# ══════════════════════════════════════════════════════
#  COMMANDS MAPPING & ROUTER REGISTRY
# ══════════════════════════════════════════════════════
COMMAND_KEYWORDS = [
    "start", "search", "bananalogic", "quiz", "leaderboard", "lb", "nw", 
    "gay", "couple", "pump", "dump", "gm", "tictac", "mine", "news", 
    "ainews", "technews", "price", "volume", "topgainers", "toplosers", 
    "chart", "qr", "scanqr", "resize", "compress", "watermark", "imginfo", 
    "botstats", "countdown", "daysleft", "encode", "decode"
]

async def execute_fuzzy_command_routing(text: str, u: Update, c: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Evaluates raw typographic inputs using rapidfuzz algorithms to intercept spelling mistakes.
    """
    if not text.startswith("/") or len(text) < 2: return False
    raw_cmd = text.split()[0].lower().lstrip("/")
    if "@" in raw_cmd: raw_cmd = raw_cmd.split("@")[0]
    
    if raw_cmd in COMMAND_KEYWORDS: return False
    
    # RapidFuzz match extraction verification parameters matching logic loop
    match = process.extractOne(raw_cmd, COMMAND_KEYWORDS, scorer=fuzz.WRatio)
    if match and match[1] >= 78:
        corrected_command = match[0]
        logger.info(f"🔀 Fuzzy Match Routing Intercepted: original `/{raw_cmd}` mapped to `/{corrected_command}` ({match[1]}% confidence profile)")
        
        # Mapping logic execution translation table structures
        mapping_table = {
            "start": start_handler, "search": search_handler, "bananalogic": bananalogic_handler,
            "quiz": quiz_handler, "leaderboard": lb_handler, "lb": lb_handler, "nw": nw_handler,
            "gay": fun_dispatcher, "couple": fun_dispatcher, "pump": pump_dump_handler, "dump": pump_dump_handler,
            "gm": gm_handler, "tictac": tictac_handler, "mine": mine_handler, "news": crypto_news_handler,
            "ainews": ai_news_handler, "technews": tech_news_handler, "price": crypto_price_handler,
            "volume": crypto_volume_handler, "topgainers": crypto_movers_handler, "toplosers": crypto_movers_handler,
            "chart": crypto_chart_handler, "qr": qr_generate_handler, "scanqr": qr_scan_handler,
            "resize": img_resize_handler, "compress": img_compress_handler, "watermark": img_watermark_handler,
            "imginfo": img_info_handler, "botstats": bot_stats_handler, "countdown": date_utils_handler,
            "daysleft": date_utils_handler, "encode": binary_encode_handler, "decode": binary_decode_handler
        }
        
        handler_fn = mapping_table.get(corrected_command)
        if handler_fn:
            # Reconstruct contextual arguments for the mapped function target block execution pipelines safely
            orig_args = text.split()[1:]
            c.args = orig_args
            await handler_fn(u, c)
            return True
    return False

# ══════════════════════════════════════════════════════
#  CORE TELEGRAM UPDATE MONITOR HANDLER LOOP
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
        if counts[cid] % 6 == 0: await safe_react(c.bot, u.effective_chat.id, u.message.message_id)

        text = (u.message.text or u.message.caption or "").strip()
        
        # Intercept typo commands via Fuzzy Routing Matrix pipeline overlays before standard handling
        if text.startswith("/"):
            intercepted = await execute_fuzzy_command_routing(text, u, c)
            if intercepted: return

        bot_username = bot_status.get("username", "")
        text_low = text.lower()
        contains_beluga = "beluga" in text_low
        contains_username = bool(bot_username) and (bot_username in text_low or f"@{bot_username}" in text_low)
        is_reply_to_bot = (u.message.reply_to_message is not None and u.message.reply_to_message.from_user is not None and u.message.reply_to_message.from_user.id == c.bot.id)

        if text and (contains_beluga or contains_username or is_reply_to_bot):
            try: await asyncio.wait_for(c.bot.send_chat_action(u.effective_chat.id, "typing"), timeout=4.0)
            except Exception: pass

            emoji = "😼"
            try: emoji = await ai_emoji(text)
            except Exception: pass
            try: await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
            except Exception: pass

            # Generate dynamically optimized custom context prompt stack via real-time TextBlob analytics arrays
            personalized_system_prompt = process_linguistic_sentiment_analysis(text)

            reply = "Meow! 🐾"
            try: reply = await ai(personalized_system_prompt, text, "Meow! 🐾")
            except Exception as e: logger.error(f"[monitor/ai] {e}")

            try: await u.message.reply_text(reply)
            except Exception as e: logger.error(f"[monitor/reply] {e}")

            try: await send_random_sticker(c.bot, u.effective_chat.id)
            except Exception: pass

        bot_status["message_count"] += 1; bot_status["last_update"] = datetime.now()
    except Exception as e:
        logger.error(f"[monitor] {e}", exc_info=True); bot_status["error_count"] += 1

# ══════════════════════════════════════════════════════
#  LEGACY MANDATORY CORE HANDLERS
# ══════════════════════════════════════════════════════
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    text = (
        "✨✨✨✨✨✨✨✨✨✨✨✨✨\n"
        "     🐱 BELUGA QUANT QUANTUM BOT 🐱\n"
        "✨✨✨✨✨✨✨✨✨✨✨✨✨\n\n"
        "Hi! I'm Beluga 🐾 Your friendly cat companion from Team Oldy Crypto!\n\n"
        "🎮 *INTERACTIVE GAMES*\n"
        " `/tictac` — Tic Tac Toe (PvP Framework vs Bot)\n"
        " `/mine` — Strategic Minesweeper Field\n\n"
        "📈 *CCXT METRICS AND EXCHANGE FEEDS*\n"
        " `/price <ticker>` — Fetch Token Exchange Value Spot Report\n"
        " `/volume <ticker>` — View Current Transaction Volumes\n"
        " `/topgainers` | `/toplosers` — Highest Volatility Swings\n"
        " `/chart <ticker> <timeframe>` — Candlestick Engine Render\n\n"
        "🛰 *RSS MULTI-STREAM INTELLIGENCE*\n"
        " `/news` — Crypto Headlines Matrix Feed\n"
        " `/ainews` — Intelligence Processing Logs\n"
        " `/technews` — Global Infrastructure Feed\n\n"
        "🧱 *UTILITIES MATRIX CAPABILITIES*\n"
        " `/qr <text>` | `/scanqr` — QR Transformation Engine\n"
        " `/imginfo` | `/resize` | `/compress` | `/watermark` — Image Tools\n"
        " `/encode` | `/decode` — Base-2 Binary Stream Transforms\n"
        " `/botstats` — Show Detailed pandas Analytics Array\n"
        " `/countdown` | `/daysleft` — Time Bounds Utilities\n"
        " `/quiz` — Analytical Trivia Quiz\n\n"
        "🏆 *COMPETITION RECORDS MATRIX*\n"
        " `/lb` — Current Group Leaderboard Array\n"
        " `/gm` — Record Daily Attendance (Group Owner Constraint)\n"
        " `/couple` | `/gay` — Real-Time Identity Check\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🐾 _Just mention my name to initiate interactive dialogue loops anytime!_"
    )
    await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("🐱 Usage: `/search computational query context`", parse_mode=ParseMode.MARKDOWN); return
    query = parts[1].strip(); cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "🔍")
    sm = await u.message.reply_text("🔎 *Web Search*\n\nOperation: Initialization\nProgress: Gathering structural indexed information sources...", parse_mode=ParseMode.MARKDOWN)
    await c.bot.send_chat_action(cid, "typing")
    loop = asyncio.get_running_loop()
    wiki, goog = await asyncio.gather(loop.run_in_executor(None, wiki_summary, query), loop.run_in_executor(None, google_search, query))
    try: await sm.edit_text("🔎 *Web Search*\n\nOperation: Summarization\nProgress: AI synthesizing factual data packets...", parse_mode=ParseMode.MARKDOWN)
    except Exception: pass
    summary = await web_summarise(query, wiki, goog, "Smart assistant. Write a clean concise summary. Max 250 words.")
    if summary: await sm.edit_text(f"🔍 *{query}*\n\n{summary}", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    else: await sm.edit_text("😿 No operational data entries matched that string criteria.", parse_mode=ParseMode.MARKDOWN)

async def bananalogic_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message: return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("🍌 *BananaLogic Request Reject*\nUsage: `/bananalogic explain quantum loop computing mechanics`", parse_mode=ParseMode.MARKDOWN); return
    query = parts[1].strip(); cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "🍌")
    sm = await u.message.reply_text("🍌 *BananaLogic*\n\nOperation: AI Intelligence\nProgress: Scraping the web parsing index configurations...", parse_mode=ParseMode.MARKDOWN)
    await c.bot.send_chat_action(cid, "typing")
    loop = asyncio.get_running_loop()
    wiki, goog = await asyncio.gather(loop.run_in_executor(None, wiki_summary, query), loop.run_in_executor(None, google_search, query))
    if not goog["found"] or not goog["snippets"]:
        try:
            ddg = await loop.run_in_executor(None, duckduckgo_search, query)
            if ddg: goog["snippets"].extend(ddg[:3]); goog["found"] = True
        except Exception: pass
    try: await sm.edit_text("🍌 *BananaLogic*\n\nOperation: Generation\nProgress: Generating answer based on facts inference loops...", parse_mode=ParseMode.MARKDOWN)
    except Exception: pass
    answer = await web_summarise(query, wiki, goog, BANANA_PROMPT, max_tok=600)
    if answer: await sm.edit_text(f"🍌 *BananaLogic*\n\n{answer}", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    else: await sm.edit_text("🍌 System search loops executed with no definitive response mapping, meow! 🐾", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════════════
#  GLOBAL ERROR RESOLUTION LAYER
# ══════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut, Forbidden)): return
    if isinstance(err, RetryAfter):
        await asyncio.sleep(err.retry_after + 1); return
    if isinstance(err, BadRequest):
        if "not modified" in str(err).lower(): return
    bot_status["error_count"] += 1
    tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logger.error(f"[Err] Interface Context Runtime Exception: {err}\n{tb_str}")
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ *Beluga Architecture Fault Logs*\n`{err.__class__.__name__}: {str(err)[:150]}`", parse_mode=ParseMode.MARKDOWN)
        except Exception: pass

# ══════════════════════════════════════════════════════
#  MAIN ENTRY APPLICATION EXECUTION BOOTSTRAP
# ══════════════════════════════════════════════════════
async def main():
    logger.info("🐱 INITIALIZING BELUGA HIGH-PERFORMANCE BOT STACK SYSTEM PROFILES PRODUCTION v7.6.1")
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    app = TGApp.builder().token(BOT_TOKEN).build()

    # Core system mappings execution parameters matrix registration loops
    app.add_handler(CommandHandler("start",               start_handler))
    app.add_handler(CommandHandler("search",              search_handler))
    app.add_handler(CommandHandler("bananalogic",         bananalogic_handler))
    app.add_handler(CommandHandler("quiz",                quiz_handler))
    app.add_handler(CommandHandler(["lb","leaderboard"],  lb_handler))
    app.add_handler(CommandHandler("nw",                  nw_handler))
    app.add_handler(CommandHandler(["gay","couple"],      fun_dispatcher))
    app.add_handler(CommandHandler(["pump","dump"],       pump_dump_handler))
    app.add_handler(CommandHandler("gm",                  gm_handler))
    app.add_handler(CommandHandler("tictac",              tictac_handler))
    app.add_handler(CommandHandler("mine",                mine_handler))
    
    # Newly Integrated Command Set Mapping Logic Registries
    app.add_handler(CommandHandler("news",                crypto_news_handler))
    app.add_handler(CommandHandler("ainews",              ai_news_handler))
    app.add_handler(CommandHandler("technews",            tech_news_handler))
    app.add_handler(CommandHandler("price",               crypto_price_handler))
    app.add_handler(CommandHandler("volume",              crypto_volume_handler))
    app.add_handler(CommandHandler(["topgainers","toplosers"], crypto_movers_handler))
    app.add_handler(CommandHandler(["chart", "chart5m", "chart15m", "chart1h", "chart4h", "chart1d"], crypto_chart_handler))
    app.add_handler(CommandHandler("qr",                  qr_generate_handler))
    app.add_handler(CommandHandler("scanqr",              qr_scan_handler))
    app.add_handler(CommandHandler("resize",              img_resize_handler))
    app.add_handler(CommandHandler("compress",            img_compress_handler))
    app.add_handler(CommandHandler("watermark",           img_watermark_handler))
    app.add_handler(CommandHandler("imginfo",             img_info_handler))
    app.add_handler(CommandHandler("botstats",            bot_stats_handler))
    app.add_handler(CommandHandler(["countdown", "daysleft"], date_utils_handler))
    app.add_handler(CommandHandler("encode",              binary_encode_handler))
    app.add_handler(CommandHandler("decode",              binary_decode_handler))

    app.add_handler(CallbackQueryHandler(ttt_ready_callback, pattern=r"^ttt_ready:"))
    app.add_handler(CallbackQueryHandler(ttt_callback,       pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(gm_callback,        pattern=r"^gm:"))
    app.add_handler(CallbackQueryHandler(mine_callback,      pattern=r"^mine:"))

    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)

    await app.initialize()
    await app.start()

    try:
        me = await app.bot.get_me()
        bot_status["username"] = me.username.lower()
        logger.info(f"🤖 Operational Bot Core Identity Authenticated: @{me.username}")
    except Exception as e: logger.warning(f"[Startup Initialization] {e}")

    await init_stickers(app.bot)
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    bot_status["running"] = True

    stop_evt = asyncio.Event()
    try:
        import signal
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT,  stop_evt.set)
    except Exception: pass

    async def periodic_cleanup():
        while not stop_evt.is_set():
            await asyncio.sleep(60); await cleanup_expired_games()

    cleanup_task = asyncio.create_task(periodic_cleanup())
    sync_task    = asyncio.create_task(periodic_github_sync())

    await stop_evt.wait()

    cleanup_task.cancel(); sync_task.cancel()
    bot_status["running"] = False

    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try: await fn()
        except Exception: pass

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception: sys.exit(1)
