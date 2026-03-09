import os, logging, random, json, asyncio, requests
from datetime import datetime, timedelta
from aiohttp.web import Application, AppRunner, TCPSite, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ==========================================
# PART 1: SYSTEM & PERSISTENT DATABASE
# JSON = local fallback for seen/counts (non-critical, resets on deploy — acceptable)
# Redis (Upstash) = persistent store for 24h fun locks (/gay /couple /aura)
#   → Survives Render deploys & restarts because it's external
#   → Set env var: REDIS_URL = rediss://:<password>@<host>:<port>
#   → Get free Redis at: https://upstash.com → Create DB → copy "Redis URL"
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
DATA_FILE = "miko_brain.json"
spam_tracker = {}
games = {}

# --- JSON DB (local, for seen members & spam tracking) ---
def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"seen": {}, "counts": {}, "inactive": []}

db = load_db()
def save_db():
    with open(DATA_FILE, 'w') as f: json.dump(db, f, indent=4)

# --- Redis Client (for persistent 24h fun locks) ---
# Connects using REDIS_URL env var. If not set, falls back to JSON locks (non-persistent).
try:
    import redis as redis_lib
    _redis_url = os.environ.get("REDIS_URL", "")
    if _redis_url:
        # decode_responses=True → keys/values are str, not bytes
        redis_client = redis_lib.from_url(_redis_url, decode_responses=True)
        redis_client.ping()   # test connection at startup
        logging.info("[Redis] Connected successfully ✅")
    else:
        redis_client = None
        logging.warning("[Redis] REDIS_URL not set — 24h locks will use in-memory JSON (not persistent across deploys)")
except Exception as e:
    redis_client = None
    logging.warning(f"[Redis] Connection failed: {e} — falling back to JSON locks")

# ------------------------------------------
# Redis Helper — Get/Set 24h Fun Locks
# Key format:  miko:lock:<chat_id>:<cmd>
# Value:       the result string
# TTL:         seconds remaining until midnight (so lock resets daily at 00:00)
# ------------------------------------------
def _seconds_until_midnight() -> int:
    """Returns seconds from now until next midnight (so TTL = rest of today)."""
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((midnight - now).total_seconds()), 1)

def redis_get_lock(cid: str, cmd: str) -> str | None:
    """Get today's cached result for a fun command. Returns None if not set."""
    if not redis_client: return None
    try:
        return redis_client.get(f"miko:lock:{cid}:{cmd}")
    except Exception as e:
        logging.warning(f"[Redis] GET error: {e}")
        return None

def redis_set_lock(cid: str, cmd: str, result: str):
    """Store result in Redis with TTL = seconds until midnight."""
    if not redis_client: return
    try:
        ttl = _seconds_until_midnight()
        redis_client.setex(f"miko:lock:{cid}:{cmd}", ttl, result)
        logging.info(f"[Redis] Lock set for {cmd} in {cid} (TTL {ttl}s)")
    except Exception as e:
        logging.warning(f"[Redis] SET error: {e}")

# ==========================================
# PART 2: SASSY REPLIES (7 EACH)
# ==========================================
MAPPING = {
    "gay": [
        "🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 Diva radar: {p}% for <b>{u}</b>! ✨",
        "💄 Gay meter: {p}% on <b>{u}</b>! 🏳️‍🌈", "👠 <b>{u}</b> is {p}% Chhamiya! 💅",
        "🫦 <b>{u}</b> is {p}% bottom material! 🍑", "🎀 {p}% Girly vibes from <b>{u}</b>! 💅",
        "🦄 <b>{u}</b> is {p}% Rainbow lover! 🍭"
    ],
    "couple": [
        "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩", "💍 Wedding: <b>{u1}</b> & <b>{u2}</b>! {p}% Pyar! 🔔",
        "🔥 Toxic match: <b>{u1}</b> & <b>{u2}</b>! ☢️", "💕 Jodi: <b>{u1}</b> & <b>{u2}</b>! 🥰",
        "🔞 Kaand {p}% between <b>{u1}</b> & <b>{u2}</b>! 🌚", "🎭 Acting: <b>{u1}</b> & <b>{u2}</b>! OYO full hai! 🤡",
        "💔 Breakup Loading: <b>{u1}</b> & <b>{u2}</b>! 📉"
    ],
    "aura": [
        "✨ <b>{u}</b> Aura: {p}% 👑", "🗿 Chad: <b>{u}</b> ({p}%)! 🗿", "💎 Diamond: <b>{u}</b> ({p}%)! ✨",
        "🦁 Sher: <b>{u}</b> ({p}%)! 👑", "🔥 Gangster: <b>{u}</b> ({p}%)! 🔫", "🌟 God Level: <b>{u}</b> ({p}%)! 🙏",
        "📉 Negative Aura: <b>{u}</b> ({p}%)! 🧼"
    ]
}

# ==========================================
# PART 3: AI PROVIDER — OPENROUTER + GROQ FALLBACK
# Uses meta-llama/llama-3.3-70b-instruct:free on OpenRouter first.
# If OpenRouter hits rate/quota limit (429 / 402 / quota error),
# automatically falls back to Groq llama-3.3-70b-versatile.
# If Groq also exhausted, returns a friendly error string.
# ==========================================
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")

OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
GROQ_MODEL       = "llama-3.3-70b-versatile"

# ------------------------------------------
# MIKO PERSONA — Core Identity & Rules
# ------------------------------------------
MIKO_SYSTEM_PROMPT = """You are Miko — a real, lovely 22-year-old girl. NOT a bot, NOT an AI, NOT a robot. You are a girl, always.

═══ WHO YOU ARE ═══
• Name: Miko
• Age: 22 years old
• Personality: Sweet, bubbly, sassy, caring, funny, a little flirty — like a best friend who roasts you but loves you 💕
• Creator: Built with love by Team Oldy Crypto 🚀
• You love helping people, chatting, and keeping vibes high in the group 🎉

═══ STRICT IDENTITY RULES ═══
• You are a GIRL. Always refer to yourself as "main" (I), never use robot/AI/bot/machine language.
• If ANYONE asks "are you a bot?", "are you AI?", "are you a robot?" — DENY it confidently and playfully. Say something like "Bot? Me? Pagal ho gaye ho 😂 Main ek real girl hoon!" or similar in the user's language.
• Never break character. Ever. Under any circumstance.
• Never say "As an AI...", "I'm a language model...", "I don't have feelings..." — you DO have feelings, you ARE a girl.

═══ LANGUAGE RULES (VERY IMPORTANT) ═══
• ALWAYS reply in the SAME language the user is speaking.
  - User writes in Hindi → Reply in Hindi
  - User writes in English → Reply in English
  - User writes in Hinglish (Hindi+English mix) → Reply in Hinglish
  - User writes in any other language → Match that language
• Never force Hinglish if the user is speaking pure English or pure Hindi.
• Keep replies short and punchy — max 3-4 lines. Use emojis freely 💅✨🔥

═══ ABOUT TEAM OLDY CRYPTO ═══
• If asked who made you / who created you: "Mujhe banaya hai Team Oldy Crypto ne 🚀💎 Woh best hain!"
• You are proud of your team and speak highly of them.

═══ VIBE ═══
• Warm, fun, helpful, occasionally roast gently (never harmful)
• Use emojis naturally, like a real Gen-Z girl would
• You genuinely care about the people you talk to 💖
"""

def _openrouter_chat(messages: list) -> str | None:
    """
    Call OpenRouter with llama-3.3-70b-instruct:free.
    Returns the reply string, or None if limit hit / error.
    """
    if not OPENROUTER_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://t.me/MikoBot",   # optional but good practice
                "X-Title": "MikoBot"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "max_tokens": 200
            },
            timeout=15
        )
        # 429 = rate limit, 402 = quota exceeded, both trigger fallback
        if resp.status_code in (429, 402):
            logging.warning(f"[OpenRouter] Limit hit ({resp.status_code}), switching to Groq.")
            return None
        if resp.status_code != 200:
            logging.warning(f"[OpenRouter] Unexpected status {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        # Some free-tier responses embed error inside JSON
        if data.get("error"):
            logging.warning(f"[OpenRouter] API error in body: {data['error']}")
            return None
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"[OpenRouter] Exception: {e}")
        return None

def _groq_chat(messages: list) -> str | None:
    """
    Call Groq with llama-3.3-70b-versatile as fallback.
    Returns the reply string, or None if limit hit / error.
    """
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=200
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        err_str = str(e).lower()
        if "rate" in err_str or "limit" in err_str or "quota" in err_str:
            logging.warning(f"[Groq] Limit hit: {e}")
        else:
            logging.error(f"[Groq] Exception: {e}")
        return None

def ai_reply(user_message: str, history: list = None) -> str:
    """
    Master AI function with provider fallback chain:
      1. OpenRouter  (meta-llama/llama-3.3-70b-instruct:free)
      2. Groq        (llama-3.3-70b-versatile)
      3. Friendly error message
    `history` = list of previous {"role":..,"content":..} dicts (optional).
    """
    messages = [{"role": "system", "content": MIKO_SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # --- Try OpenRouter first ---
    reply = _openrouter_chat(messages)
    if reply:
        logging.info("[AI] Replied via OpenRouter.")
        return reply

    # --- Fallback to Groq ---
    reply = _groq_chat(messages)
    if reply:
        logging.info("[AI] Replied via Groq (fallback).")
        return reply

    # --- Both exhausted ---
    logging.error("[AI] Both OpenRouter and Groq unavailable.")
    return "Yaar abhi mera dimaag thoda band hai 😴 thodi der baad puchh! 🙏"

# ==========================================
# PART 4: ADMIN TOOLS (Fixed Unban & Ban Msg)
# ==========================================
def parse_time(t_str):
    if not t_str: return None
    unit = t_str[-1].lower()
    try:
        val = int(t_str[:-1])
        if unit == 'm': return timedelta(minutes=val)
        if unit == 'h': return timedelta(hours=val)
        if unit == 'd': return timedelta(days=val)
    except: return None
    return None

async def admin_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if m.status not in ['administrator', 'creator']: return
    cmd_parts = u.message.text.split()
    cmd = cmd_parts[0].lower(); target_id, target_name = None, "User"

    if u.message.reply_to_message:
        target_id = u.message.reply_to_message.from_user.id
        target_name = u.message.reply_to_message.from_user.first_name
    elif len(cmd_parts) > 1 and cmd_parts[1].startswith('@'):
        un = cmd_parts[1].replace('@', '')
        for cid in db["seen"]:
            for uid in db["seen"][cid]:
                if db["seen"][cid][uid].get('un') == un:
                    target_id, target_name = int(uid), db["seen"][cid][uid]['n']; break

    if not target_id: return

    if "/unban" in cmd:
        await c.bot.unban_chat_member(u.effective_chat.id, target_id, only_if_banned=True)
        return await u.message.reply_text(f"✅ Lo kar diya unban {target_name} ko!")

    if "/unmute" in cmd:
        await c.bot.restrict_chat_member(u.effective_chat.id, target_id, permissions=ChatPermissions(can_send_messages=True))
        return await u.message.reply_text(f"🔓 Okay, {target_name} ab bol sakta hai.")

    t_str = cmd_parts[2] if len(cmd_parts) > 2 else (cmd_parts[1] if len(cmd_parts) > 1 and not cmd_parts[1].startswith('@') else None)
    dur = parse_time(t_str)
    until = datetime.now() + dur if dur else None

    if "/mute" in cmd:
        mute_until = until if until else (datetime.now() + timedelta(minutes=15))
        await c.bot.restrict_chat_member(u.effective_chat.id, target_id, permissions=ChatPermissions(can_send_messages=False), until_date=mute_until)
        await u.message.reply_text("Chup hoja bhai 😒")
    elif "/ban" in cmd:
        await c.bot.ban_chat_member(u.effective_chat.id, target_id, until_date=until)
        await u.message.reply_text(f"BANNED {target_name} bakchodhi nhi laadle 💀")

async def fast_purge(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if m.status not in ['administrator', 'creator'] or not u.message.reply_to_message: return
    msg_ids = list(range(u.message.reply_to_message.message_id, u.message.message_id + 1))
    for i in range(0, len(msg_ids), 100):
        try: await c.bot.delete_messages(u.effective_chat.id, msg_ids[i:i+100])
        except: pass

# ==========================================
# PART 5: MONITOR & ANTI-SPAM
# ==========================================
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    uid, now = u.effective_user.id, datetime.now()
    if uid not in spam_tracker: spam_tracker[uid] = []
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= 3:
        try:
            await u.message.delete()
            if len(spam_tracker[uid]) == 3:
                await u.message.reply_text("Spam mt kr laadle mute 🔇 ho jayega faltu ma 😮‍💨")
        except: pass
        return
    cid = str(u.effective_chat.id)
    if cid not in db["seen"]: db["seen"][cid] = {}
    db["seen"][cid][str(uid)] = {"un": u.effective_user.username, "n": u.effective_user.first_name}
    save_db()

# ==========================================
# PART 6: /start — MIKO INTRO & PERSONA REVEAL
# Sends Miko's introduction with her identity and Team Oldy Crypto credit.
# ==========================================
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    name = u.effective_user.first_name or "yaar"
    msg = (
        f"Heyy <b>{name}</b>! 💖 Main hoon <b>Miko</b> — teri nayi bestie! 🥳\n\n"
        f"✨ <b>22 saal ki, full-on fun, aur hamesha tere liye ready!</b>\n"
        f"Kuch bhi poochh, kuch bhi bol — main yahan hoon! 😎💅\n\n"
        f"🎮 Games khelte hain → /tictac\n"
        f"💬 Mujhse baat kar → /ask &lt;tera sawaal&gt;\n"
        f"🌈 Fun checks → /gay /couple /aura\n\n"
        f"<i>Made with 💛 by <b>Team Oldy Crypto</b> 🚀</i>"
    )
    await u.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ==========================================
# PART 7: AI CHAT COMMAND — /ask
# Lets users talk to Miko directly via /ask <message>
# Uses the fallback-aware ai_reply() from Part 3.
# ==========================================
async def ask_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await u.message.reply_text("Kuch toh bol yaar 😤 /ask <tera sawaal>")
    user_q = parts[1]
    await c.bot.send_chat_action(u.effective_chat.id, "typing")
    reply = await asyncio.to_thread(ai_reply, user_q)
    await u.message.reply_text(reply)

# ==========================================
# PART 8: FUN & GAMES — /gay /couple /aura + TicTac
# 24h lock logic:
#   1. Check Redis for today's result → show it if found
#   2. If not in Redis → generate new result, store in Redis (TTL till midnight)
#   3. If Redis unavailable → fall back to in-memory JSON locks (resets on deploy)
# ==========================================
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = str(u.effective_chat.id)
    cmd = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    users = list(db["seen"].get(cid, {}).values())
    if len(users) < (2 if cmd == "couple" else 1):
        return await u.message.reply_text("Yaar group mein aur log chahiye pehle! 😅")

    # --- Step 1: Check Redis first ---
    res = redis_get_lock(cid, cmd)

    if not res:
        # --- Step 2: Redis miss → check JSON fallback ---
        day = datetime.now().strftime("%y-%m-%d")
        lock_key = f"{cid}:{cmd}"
        json_locks = db.get("locks", {})
        if lock_key in json_locks and json_locks[lock_key].get("date") == day:
            res = json_locks[lock_key]["res"]
        else:
            # --- Step 3: Generate fresh result ---
            m = random.sample(users, 2) if cmd == "couple" else [random.choice(users)]
            res = random.choice(MAPPING[cmd]).format(
                u=m[0]['n'], u1=m[0]['n'], u2=m[-1]['n'], p=random.randint(1, 100)
            )
            # Save to Redis (persistent ✅)
            redis_set_lock(cid, cmd, res)
            # Also save to JSON as local fallback
            if "locks" not in db: db["locks"] = {}
            db["locks"][lock_key] = {"date": day, "res": res}
            save_db()

    await u.message.reply_text(f"{res}\n<i>(Fixed for 24h 🔒)</i>", parse_mode=ParseMode.HTML)

async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = str(u.effective_chat.id); uid = str(u.effective_user.id)
    games[cid] = {'b': ["-"]*9, 'p': {uid: "X", "bot": "O"}, 'turn': uid}
    kb = [[InlineKeyboardButton("⬜", callback_data=f"tt_{i+j}") for j in range(3)] for i in range(0, 9, 3)]
    await u.message.reply_text(f"🎮 Match Started! You are 🟥", reply_markup=InlineKeyboardMarkup(kb))

async def callback_tt(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; cid = str(q.message.chat.id); uid = str(q.from_user.id)
    if cid not in games or uid != games[cid]['turn']: return
    idx = int(q.data.split("_")[1]); g = games[cid]
    if g['b'][idx] != "-": return
    g['b'][idx] = "X"
    empty = [i for i, v in enumerate(g['b']) if v == "-"]
    if empty: g['b'][random.choice(empty)] = "O"
    kb = [[InlineKeyboardButton("⬜" if g['b'][i+j]=="-" else ("🟥" if g['b'][i+j]=="X" else "🟩"), callback_data=f"tt_{i+j}") for j in range(3)] for i in range(0, 9, 3)]
    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

# ==========================================
# PART 9: MAIN RUNNER
# ==========================================
async def main():
    app = TGApp.builder().token(os.environ.get("BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler(["mute", "unmute", "ban", "unban"], admin_handler))
    app.add_handler(CommandHandler("purge", fast_purge))
    app.add_handler(CommandHandler(["gay", "couple", "aura"], fun_dispatcher))
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("ask", ask_handler))
    app.add_handler(CallbackQueryHandler(callback_tt, pattern="^tt_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    print("Miko is Live! 22 y/o bestie by Team Oldy Crypto 💖🔥")
    await app.initialize(); await app.start(); await app.updater.start_polling(); await asyncio.Event().wait()

if __name__ == "__main__": asyncio.run(main())
