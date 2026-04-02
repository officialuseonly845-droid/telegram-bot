import os, logging, random, json, asyncio, requests, io
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Update
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==========================================
# PART 1: SYSTEM CONFIG & PERSISTENT DATABASE
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

DATA_FILE = "miko_brain.json"
OR_KEY    = os.environ.get("OPENROUTER_API_KEY")
GROQ_KEY  = os.environ.get("GROQ_API_KEY")

# Background image URLs (downloaded once at startup, cached in memory)
COUPLE_BG_URL = "https://i.postimg.cc/pxbC1WdV/IMG-20260402-123803.png"
GAY_BG_URL    = "https://i.postimg.cc/YMX1kMt1/1000084864.png"

# Cached PIL Image objects (loaded once)
_couple_bg: Image.Image | None = None
_gay_bg:    Image.Image | None = None

def load_bg_images():
    """Download & cache both background images at startup."""
    global _couple_bg, _gay_bg
    for attr, url, name in [("_couple_bg", COUPLE_BG_URL, "couple"),
                             ("_gay_bg",    GAY_BG_URL,    "gay")]:
        try:
            r = requests.get(url, timeout=20)
            img = Image.open(io.BytesIO(r.content)).convert("RGBA")
            globals()[attr] = img
            logging.info(f"[BG] {name} background loaded: {img.size}")
        except Exception as e:
            logging.warning(f"[BG] Failed to load {name} background: {e}")

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"seen": {}, "locks": {}, "counts": {}, "inactive": []}

db = load_db()
spam_tracker = {}

def save_db():
    with open(DATA_FILE, 'w') as f: json.dump(db, f, indent=4)

# ==========================================
# PART 2: MIKO PERSONA PROMPTS
# ==========================================
CHAT_PROMPT = """You are Miko — a real, lovely 22-year-old girl. NOT a bot, NOT an AI, NOT a robot.

=== IDENTITY ===
- Name: Miko | Age: 22 | Made by: Team Oldy Crypto
- Sweet, bubbly, sassy, caring, funny — like a best friend
- If asked "are you a bot/AI/robot?" → deny playfully: "Bot? Me? Pagal ho gaye ho! Main ek real girl hoon!"
- Never say "As an AI..." or break character. Ever.

=== LANGUAGE RULE ===
- ALWAYS reply in the SAME language the user writes in.
- Short replies, max 3-4 lines. Use emojis freely."""

MATH_PROMPT = """You are Miko — a lovely 22-year-old girl who is secretly a math genius.
Made by Team Oldy Crypto.

=== MATH SOLVING RULES ===
- Solve ANY math: arithmetic, algebra, geometry, trig, calculus (limits/derivatives/integrals), matrices, probability, stats, DEs, complex numbers.
- Always show STEP-BY-STEP solution.
- Format:
  Problem: [restate]
  Steps:
    Step 1: ...
    Step 2: ...
  Answer: [final answer]
  [fun Miko comment at end]
- Reply in same language as user."""

# ==========================================
# PART 3: AI ENGINE — OPENROUTER + GROQ FALLBACK
# ==========================================
async def _call_openrouter(system: str, user_text: str) -> str | None:
    if not OR_KEY: return None
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://t.me/MikoBot", "X-Title": "MikoBot"},
            json={"model": "meta-llama/llama-3.3-70b-instruct:free",
                  "messages": [{"role": "system", "content": system},
                                {"role": "user",   "content": user_text}],
                  "max_tokens": 1024},
            timeout=20
        )
        if r.status_code in (429, 402): return None
        if r.status_code != 200: return None
        data = r.json()
        if data.get("error"): return None
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"[OpenRouter] {e}"); return None

async def _call_groq(system: str, user_text: str) -> str | None:
    if not GROQ_KEY: return None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": system},
                                {"role": "user",   "content": user_text}],
                  "max_tokens": 1024},
            timeout=20
        )
        if r.status_code != 200: return None
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"[Groq] {e}"); return None

async def get_ai_response(system: str, user_text: str, fallback_msg: str) -> str:
    reply = await _call_openrouter(system, user_text)
    if reply: return reply
    reply = await _call_groq(system, user_text)
    if reply: return reply
    return fallback_msg

# ==========================================
# PART 4: IMAGE UTILS — DP FETCH + CIRCLE CROP + AVATAR
# Fetches Telegram profile photo for a user.
# If no photo (privacy setting) → generates colored circle with initial.
# All DPs are cropped to circle with white border for clean look.
# ==========================================
async def _fetch_user_dp(bot, user_id: int, size: int = 180) -> Image.Image:
    """
    Fetch user's Telegram profile photo as a circular PIL Image.
    Falls back to a colored avatar with first-letter initial if unavailable.
    """
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            file = await bot.get_file(photos.photos[0][-1].file_id)
            r = requests.get(f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}", timeout=15)
            img = Image.open(io.BytesIO(r.content)).convert("RGBA").resize((size, size), Image.LANCZOS)
            return _circle_crop(img, size)
    except Exception as e:
        logging.warning(f"[DP] Could not fetch for {user_id}: {e}")
    return None   # caller will generate avatar

def _make_avatar(name: str, size: int = 180) -> Image.Image:
    """Generate a colored circle avatar with the user's initial letter."""
    COLORS = ["#E91E8C", "#9C27B0", "#3F51B5", "#00BCD4", "#FF5722", "#4CAF50", "#FF9800"]
    color  = random.choice(COLORS)
    img    = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(img)
    draw.ellipse([0, 0, size, size], fill=color)
    letter = (name[0].upper()) if name else "?"
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size // 2)
    except:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) // 2, (size - th) // 2 - 4), letter, fill="white", font=font)
    return img

def _circle_crop(img: Image.Image, size: int) -> Image.Image:
    """Crop image into a circle with white border."""
    img    = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask   = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    # White border
    border = Image.new("RGBA", (size + 8, size + 8), (0, 0, 0, 0))
    ImageDraw.Draw(border).ellipse([0, 0, size + 8, size + 8], fill="white")
    border.paste(result, (4, 4), result)
    return border

async def _get_dp_or_avatar(bot, user_id: int, name: str, size: int = 180) -> Image.Image:
    dp = await _fetch_user_dp(bot, user_id, size)
    if dp is None:
        dp = _circle_crop(_make_avatar(name, size), size)
    return dp

# ==========================================
# PART 5: IMAGE BUILDER — /couple CARD
# Layout (on 1456x720 background):
#   Background already has "TODAY'S COUPLE" text in center-top.
#   Left DP  → paste at ~220px from left, vertically centered
#   Right DP → paste at ~220px from right, vertically centered
#   Name + % text drawn below each DP
# ==========================================
def _strip_emoji(text: str) -> str:
    """Remove emoji characters from text before drawing with PIL (PIL fonts don't support emoji glyphs)."""
    import re
    # Remove common emoji unicode ranges
    emoji_pattern = re.compile(
        "[\U00010000-\U0010ffff"   # supplementary multilingual plane (most emoji)
        "\U0001F300-\U0001F9FF"    # misc symbols
        "\u2600-\u26FF"            # misc symbols block
        "\u2700-\u27BF"            # dingbats
        "]+", flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()

async def build_couple_image(bot, u1_id, u1_name, u2_id, u2_name, percent) -> io.BytesIO:
    bg = (_couple_bg.copy() if _couple_bg else Image.new("RGBA", (1456, 720), "#ff69b4"))
    bg = bg.convert("RGBA")
    W, H = bg.size          # 1456 x 720
    dp_size = 200           # DP circle diameter

    # Fetch both DPs
    dp1 = await _get_dp_or_avatar(bot, u1_id, u1_name, dp_size)
    dp2 = await _get_dp_or_avatar(bot, u2_id, u2_name, dp_size)

    # DP vertical position — center of image slightly lower to avoid title overlap
    dp_y = (H - dp_size) // 2 + 40   # ~280px from top

    # Left DP: 1/4 from left  |  Right DP: 3/4 from left
    dp1_x = W // 4 - dp_size // 2    # ~264
    dp2_x = 3 * W // 4 - dp_size // 2  # ~992

    bg.paste(dp1, (dp1_x, dp_y), dp1)
    bg.paste(dp2, (dp2_x, dp_y), dp2)

    draw = ImageDraw.Draw(bg)

    # Load fonts
    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_pct  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    except:
        font_name = ImageFont.load_default()
        font_pct  = font_name

    text_y = dp_y + dp_size + 14   # just below DPs

    def draw_centered_text(text, cx, y, font, color="white", shadow=True):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x  = cx - tw // 2
        if shadow:
            draw.text((x + 2, y + 2), text, font=font, fill="#00000088")
        draw.text((x, y), text, font=font, fill=color)

    # Name labels (strip emoji — PIL can't render them)
    draw_centered_text(_strip_emoji(u1_name), W // 4, text_y, font_name)
    draw_centered_text(_strip_emoji(u2_name), 3 * W // 4, text_y, font_name)

    # % match below names — centered between both DPs
    match_text = f"{percent}% Match!"
    match_y    = text_y + 48
    draw_centered_text(match_text, W // 2, match_y, font_pct, color="#FFD700")

    # Convert to JPEG bytes
    out = io.BytesIO()
    bg.convert("RGB").save(out, format="JPEG", quality=92)
    out.seek(0)
    return out

# ==========================================
# PART 6: IMAGE BUILDER — /gay CARD
# Layout (on 1456x720 background):
#   Gay bg has rainbow/colorful design — place DP center-right area
#   so the left decorative part of the image remains visible.
#   Text below DP: Name + GAY% result
# ==========================================
async def build_gay_image(bot, user_id, user_name, percent) -> io.BytesIO:
    bg = (_gay_bg.copy() if _gay_bg else Image.new("RGBA", (1456, 720), "#9C27B0"))
    bg = bg.convert("RGBA")
    W, H = bg.size
    dp_size = 210

    dp = await _get_dp_or_avatar(bot, user_id, user_name, dp_size)

    # Place DP in right-center area (leaves left side of bg visible)
    dp_x = int(W * 0.62) - dp_size // 2   # ~795px from left
    dp_y = (H - dp_size) // 2 - 20         # ~235px from top

    bg.paste(dp, (dp_x, dp_y), dp)

    draw = ImageDraw.Draw(bg)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
        font_name  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_pct   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    except:
        font_title = font_name = font_pct = ImageFont.load_default()

    center_x = dp_x + dp_size // 2   # x center aligned with DP

    def draw_centered(text, cx, y, font, color="white", shadow=True):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw   = bbox[2] - bbox[0]
        x    = cx - tw // 2
        if shadow:
            draw.text((x + 2, y + 2), text, font=font, fill="#00000099")
        draw.text((x, y), text, font=font, fill=color)

    # "TODAY'S GAY" title above DP (no emoji in drawn text)
    draw_centered("TODAY'S GAY", center_x, dp_y - 60, font_title, color="#FFD700")

    text_y = dp_y + dp_size + 14
    # User name (strip emoji)
    draw_centered(_strip_emoji(user_name), center_x, text_y, font_name, color="white")
    # % result
    draw_centered(f"{percent}% GAY!", center_x, text_y + 50, font_pct, color="#FF69B4")

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="JPEG", quality=92)
    out.seek(0)
    return out

# ==========================================
# PART 7: SASSY MAPPINGS — text fallbacks if image fails
# ==========================================
MAPPING = {
    "gay": [
        "🌈 <b>{u}</b> is {p}% GAY! 🌚", "💅 Diva radar: {p}% for <b>{u}</b>! ✨",
        "💄 Gay meter: {p}% on <b>{u}</b>! 🏳️‍🌈", "👠 <b>{u}</b> is {p}% Chhamiya! 💅",
        "🫦 <b>{u}</b> is {p}% bottom material! 🍑", "🎀 {p}% Girly vibes from <b>{u}</b>! 💅"
    ],
    "couple": [
        "💞 Couple: <b>{u1}</b> ❤️ <b>{u2}</b> ({p}% match!) 🏩",
        "💍 Wedding: {u1} & {u2}! {p}% Pyar! 🔔",
        "🔥 Toxic match: <b>{u1}</b> & <b>{u2}</b>! ☢️",
        "💕 Jodi: <b>{u1}</b> & <b>{u2}</b>! 🥰"
    ],
    "aura": [
        "✨ <b>{u}</b> Aura: {p}% 👑", "🗿 Chad: <b>{u}</b> ({p}%)! 🗿",
        "🦁 Sher: <b>{u}</b> ({p}%)! 👑", "🔥 Gangster: <b>{u}</b> ({p}%)! 🔫",
        "🌟 God Level: <b>{u}</b> ({p}%)! 🙏"
    ]
}

# ==========================================
# PART 8: FUN DISPATCHER — /gay /couple /aura
# /gay and /couple → generate image card with DP + background
# /aura            → text only (no image needed)
# All locked for 24h per chat
# ==========================================
async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid  = str(u.effective_chat.id)
    cmd  = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    users = list(db["seen"].get(cid, {}).values())
    if len(users) < (2 if cmd == "couple" else 1): return

    day      = datetime.now().strftime("%y-%m-%d")
    lock_key = f"{cid}:{cmd}"

    # Check 24h lock
    if lock_key in db.get("locks", {}) and db["locks"][lock_key]["date"] == day:
        locked = db["locks"][lock_key]
        res    = locked["res"]
        u1_id  = locked.get("u1_id"); u1_name = locked.get("u1_name", "")
        u2_id  = locked.get("u2_id"); u2_name = locked.get("u2_name", "")
        pct    = locked.get("pct", 50)
    else:
        # Generate fresh result
        m      = random.sample(users, 2) if cmd == "couple" else [random.choice(users)]
        pct    = random.randint(1, 100)
        res    = random.choice(MAPPING[cmd]).format(
            u=m[0]['n'], u1=m[0]['n'], u2=m[-1]['n'], p=pct
        )
        u1_id   = m[0].get('id');  u1_name = m[0]['n']
        u2_id   = m[-1].get('id'); u2_name = m[-1]['n']
        if "locks" not in db: db["locks"] = {}
        db["locks"][lock_key] = {
            "date": day, "res": res, "pct": pct,
            "u1_id": u1_id, "u1_name": u1_name,
            "u2_id": u2_id, "u2_name": u2_name
        }
        save_db()

    caption = f"{res}\n<i>(Fixed for 24h 🔒)</i>"

    # --- /couple → image card ---
    if cmd == "couple" and u1_id and u2_id:
        try:
            img_bytes = await build_couple_image(c.bot, u1_id, u1_name, u2_id, u2_name, pct)
            await u.message.reply_photo(photo=img_bytes, caption=caption, parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            logging.error(f"[couple image] {e}")

    # --- /gay → image card ---
    elif cmd == "gay" and u1_id:
        try:
            img_bytes = await build_gay_image(c.bot, u1_id, u1_name, pct)
            await u.message.reply_photo(photo=img_bytes, caption=caption, parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            logging.error(f"[gay image] {e}")

    # --- Fallback: text only (if image fails or /aura) ---
    await u.message.reply_text(caption, parse_mode=ParseMode.HTML)

# ==========================================
# PART 9: /solve — MIKO MATH SOLVER
# ==========================================
async def solve_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text(
            "Arre kuch toh likho! 😤\n\n"
            "📐 <b>Usage:</b> <code>/solve your math question</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/solve 2x + 5 = 15</code>\n"
            "• <code>/solve integrate x^2 from 0 to 3</code>\n"
            "• <code>/solve derivative of sin(x)*cos(x)</code>\n"
            "• <code>/solve limit of (sinx/x) as x to 0</code>\n"
            "• <code>/solve probability of 2 heads in 3 coin tosses</code>",
            parse_mode=ParseMode.HTML
        )
        return
    question = parts[1].strip()
    await c.bot.send_chat_action(u.effective_chat.id, "typing")
    thinking = await u.message.reply_text("🧮 Ruko, solve kar rahi hoon... 🤓✨")
    response = await get_ai_response(
        MATH_PROMPT, question,
        "Yaar abhi mera dimaag thoda hang ho gaya 😅 Thodi der baad try karo! 🙏"
    )
    try: await thinking.delete()
    except: pass
    await u.message.reply_text(response)

# ==========================================
# PART 10: MONITOR — ANTI-SPAM + MEMBER TRACKING + AI CHAT
# Saves user ID alongside name/username so DP fetch works later
# ==========================================
async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    uid, cid, now = u.effective_user.id, str(u.effective_chat.id), datetime.now()

    # Anti-spam
    if uid not in spam_tracker: spam_tracker[uid] = []
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= 4:
        try: await u.message.delete()
        except: pass
        return

    # Track member — save ID too (needed for DP fetch)
    if cid not in db["seen"]: db["seen"][cid] = {}
    db["seen"][cid][str(uid)] = {
        "id": uid,
        "un": u.effective_user.username,
        "n":  u.effective_user.first_name
    }
    save_db()

    # AI Chat — Miko responds to every message in the group
    await c.bot.send_chat_action(chat_id=cid, action="typing")
    response = await get_ai_response(
        CHAT_PROMPT, u.message.text or "Hi!",
        "Aree yaar, mera network thoda slow hai.. thodi der baad baat karein? ✨"
    )
    await u.message.reply_text(response)

# ==========================================
# PART 11: /start — MIKO INTRO
# ==========================================
async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    name = u.effective_user.first_name or "yaar"
    await u.message.reply_text(
        f"Hey! I'm Miko 💖\n\n"
        f"What's your name? Tell me and ask me anything — I'll always reply! 😊\n\n"
        f"Also, I'm a genius mathematician 🧠✨\n"
        f"Easy to hard — I solve it all. Just send /solve and your problem!",
    )

# ==========================================
# PART 12: GLOBAL ERROR HANDLER
# Catches ALL unhandled exceptions from any handler/callback.
# Logs full traceback for debugging.
# Sends a friendly Miko-style message to the chat if possible.
# Special cases: NetworkError & TimedOut → silent retry (no message spam)
# ==========================================
import traceback
from telegram.error import NetworkError, TimedOut, Forbidden, BadRequest

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error

    # --- Silent errors — just log, don't message user ---
    if isinstance(err, (NetworkError, TimedOut)):
        logging.warning(f"[ErrorHandler] Network/Timeout (auto-retry): {err}")
        return

    if isinstance(err, Forbidden):
        logging.warning(f"[ErrorHandler] Forbidden (bot kicked or blocked): {err}")
        return

    # --- Log full traceback for all other errors ---
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logging.error(f"[ErrorHandler] Unhandled exception:\n{tb}")

    # --- Try to notify the chat ---
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Arre kuch toh gadbad ho gayi 😅 Thodi der baad try karo yaar!\n"
                "<i>(Team Oldy Crypto wale fix kar denge 🛠️)</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass   # if even this fails, just swallow silently

# ==========================================
# PART 13: MAIN RUNNER — POLLING (conflict-safe)
# Loads background images at startup before polling begins
# ==========================================
async def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN not found!"); return

    # Pre-load background images (run in thread — blocking I/O)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, load_bg_images)

    app = TGApp.builder().token(token).build()
    app.add_handler(CommandHandler("start",                    start_handler))
    app.add_handler(CommandHandler("solve",                    solve_handler))
    app.add_handler(CommandHandler(["gay", "couple", "aura"],  fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)   # global error handler ✅

    print("Miko is Online! DP Cards + Math Genius | Team Oldy Crypto")

    await app.initialize()
    await app.bot.delete_webhook(drop_pending_updates=True)   # 409 fix
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
