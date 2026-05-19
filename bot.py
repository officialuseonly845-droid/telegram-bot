import os
import logging
import random
import json
import asyncio
import requests
import re
import urllib.parse
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from telegram import Update, ReactionTypeEmoji
from telegram.ext import Application as TGApp, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("BelugaEngine")

DATA_FILE = "beluga_brain.json"
OR_KEY    = os.environ.get("OPENROUTER_API_KEY")
GROQ_KEY  = os.environ.get("GROQ_API_KEY")

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f: return json.load(f)
        except: pass
    return {"seen": {}, "locks": {}, "counts": {}}

db = load_db()
spam_tracker = {}

def save_db():
    with open(DATA_FILE, 'w') as f: json.dump(db, f, indent=4)

async def try_react(bot, chat_id, message_id, emoji_char=None):
    if not emoji_char:
        emoji_char = random.choice(["🐱", "🐾", "❤️", "🔥", "👍", "😻", "😼", "😂", "✨", "👀"])
    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[ReactionTypeEmoji(emoji=emoji_char)])
    except Exception as e:
        logger.warning(f"Reaction Failed: {e}")

CHAT_PROMPT = "You are Beluga—a cute, sassy, smart 2-year-old cat. NOT a bot. Built by Team Oldy Crypto. Reply based on language context instruction."
SEARCH_PROMPT = "You are Beluga—the internet-genius cat. Use search context to reply accurately in your character persona."

def inject_language_instruction(user_text: str) -> str:
    text_lower = user_text.lower()
    hinglish_tokens = ["kya", "hai", "kaise", "bhai", "batao", "kr", "rha", "tha", "ye", "wo", "tu", "tum", "ko", "nhi", "aur", "hi", "bhi"]
    if any(re.search(rf"\b{w}\b", text_lower) for w in hinglish_tokens):
        return f"{user_text}\n\n[STRICT DIRECTIVE: Reply completely in conversational Hinglish/Roman script. No Devanagari.]"
    elif any(c for c in user_text if '\u0900' <= c <= '\u097F'):
        return f"{user_text}\n\n[STRICT DIRECTIVE: Reply in Hindi Devanagari script.]"
    return f"{user_text}\n\n[STRICT DIRECTIVE: Reply in fluent English.]"

async def get_ai_response(system: str, user_text: str, fallback_msg: str) -> str:
    opt = inject_language_instruction(user_text)
    for api_func, key in [(_call_openrouter, OR_KEY), (_call_groq, GROQ_KEY)]:
        if key:
            res = await api_func(system, opt)
            if res: return res
    return fallback_msg

async def _call_openrouter(system: str, user_text: str) -> str | None:
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json"},
            json={"model": "meta-llama/llama-3.3-70b-instruct:free", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_text}], "max_tokens": 1024}, timeout=20)
        return r.json()["choices"][0]["message"]["content"].strip() if r.status_code == 200 else None
    except: return None

async def _call_groq(system: str, user_text: str) -> str | None:
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_text}], "max_tokens": 1024}, timeout=20)
        return r.json()["choices"][0]["message"]["content"].strip() if r.status_code == 200 else None
    except: return None

async def ask_ai_for_emoji(user_text: str) -> str:
    inst = f"What single emoji fits the emotion of: '{user_text}'? Reply with ONLY one emoji character, absolutely zero words."
    res = await _call_groq("Emoji Selector", inst) or await _call_openrouter("Emoji Selector", inst)
    if res:
        ems = re.findall(r'[^\w\s,.:!?\'\"()\-]+', res)
        if ems: return ems[0][0]
    return "😼"

def _google_custom_search(query: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            snips = []
            for item in soup.find_all('div', class_='result__body')[:4]:
                t = item.find('a', class_='result__url')
                s = item.find('a', class_='result__snippet')
                if t and s: snips.append(f"Title: {t.text.strip()}\nSnippet: {s.text.strip()}")
            return "\n---\n".join(snips) if snips else "No results found."
    except Exception as e: logger.error(f"Search Error: {e}")
    return "Search timed out."

GAY_TEMPLATES = [
    "🚨 **ATTENTION EVERYONE** 🚨\n\nAfter advanced investigation,\nthe council has decided that\n\n👉 **{u}** 👈\n\nis...\n\n🌈✨ **SUPER GAY** ✨🌈\n\nSentence:\nMust slay forever 💅😭",
    "📡 **GOVERNMENT ALERT** 📡\n\nOur satellites detected\nextreme rainbow activity from\n\n👉 **{u}** 👈\n\nStatus:\n\n🌈 **Certified Gay Citizen** 🌈\n\nPunishment:\nToo fabulous to handle 😭✨",
    "🧪 **SECRET LAB REPORT** 🧪\n\nSubject: **{u}**\n\nTest Results:\n\n💅 Sass Level: `999+`\n🎀 Drama Energy: `MAX`\n🌈 Gayness: `CONFIRMED`\n\nFinal Verdict:\n\n✨ **HOMOSEXUAL CREATURE DETECTED** ✨\n\n🤣🤣",
    "🚔 **FBI OPEN UP** 🚔\n\nWe received multiple reports that\n\n👉 **{u}** 👈\n\nhas been acting a little too zesty 😭\n\nAfter investigation:\n\n🌈 **GAY CONFIRMED** 🌈\n\nEvidence:\n- Types “hehe”\n- Uses cute emojis\n- Suspiciously fashionable 💅"
]

COUPLE_TEMPLATES = [
    "💘 **LOVE DETECTOR 3000** 💘\n\nAfter intense investigation,\nthe perfect couple of the group is...\n\n👉 **{u1}** ❤️ **{u2}** 👈\n\nCompatibility:\n`██████████ 100%`\n\nResult:\nMade for each other 😭✨",
    "🚨 **COUPLE ALERT** 🚨\n\nSuspicious romantic activity detected between\n\n👉 **{u1}** 💞 **{u2}** 👈\n\nEvidence:\n- Too many replies to each other 👀\n- Online together at 2AM 🌚\n- “Just friends” detected 🤡\n\nFinal Verdict:\n\n💖 **OFFICIAL GC COUPLE** 💖",
    "🧪 **RELATIONSHIP LAB REPORT** 🧪\n\nSubjects:\n**{u1}**\n**{u2}**\n\nAnalysis Results:\n\n💘 Chemistry: `MAX`\n😍 Flirting Level: `98%`\n💍 Marriage Probability: `HIGH`\n\nConclusion:\n\n✨ **PERFECT MATCH FOUND** ✨",
    "📡 **GOVERNMENT SHIP CONFIRMED** 📡\n\nOur satellites discovered hidden feelings between\n\n👉 **{u1}** ❤️‍🔥 **{u2}** 👈\n\nStatus:\n\n💞 **Secretly in love** 💞\n\nPunishment:\nForced to hold hands forever 😭"
]

async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = str(u.effective_chat.id)
    cmd = u.message.text.lower().split()[0].replace('/', '').split('@')[0]
    users = list(db["seen"].get(cid, {}).values())
    if len(users) < (2 if cmd == "couple" else 1):
        await u.message.reply_text("Meow... Need more active chat members! 😿🐾")
        return
    day = datetime.now().strftime("%y-%m-%d")
    lock_key = f"{cid}:{cmd}"
    if lock_key in db.get("locks", {}) and db["locks"][lock_key]["date"] == day:
        res = db["locks"][lock_key]["res"]
    else:
        if cmd == "couple":
            m = random.sample(users, 2)
            res = random.choice(COUPLE_TEMPLATES).format(u1=m[0]['n'], u2=m[1]['n'])
        else:
            m = [random.choice(users)]
            res = random.choice(GAY_TEMPLATES).format(u=m[0]['n'])
        if "locks" not in db: db["locks"] = {}
        db["locks"][lock_key] = {"date": day, "res": res}
        save_db()
    await u.message.reply_text(f"{res}\n\n_✍️ (Fixed for 24h 🔒)_", parse_mode=ParseMode.MARKDOWN)

async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await u.message.reply_text("Meow! Use: <code>/search query</code>", parse_mode=ParseMode.HTML)
        return
    q = parts[1].strip()
    await try_react(c.bot, u.effective_chat.id, u.message.message_id, "🔍")
    if q.startswith("http://") or q.startswith("https://"):
        st = await u.message.reply_text("🌐 Website link detected! Fetching live screenshot...")
        try:
            await u.message.reply_photo(photo=f"https://image.thum.io/get/width/1280/crop/800/{q}", caption=f"<b>Link:</b> {q}", parse_mode=ParseMode.HTML)
            await st.delete()
        except: await st.edit_text("Meow... Failed to grab screenshot! 😿")
    else:
        st = await u.message.reply_text("🐾 Querying web indexes...")
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _google_custom_search, q)
        resp = await get_ai_response(SEARCH_PROMPT, f"Query: {q}\nWeb: {raw}", "Search links fuzzy!")
        await st.delete()
        await u.message.reply_text(resp, parse_mode=ParseMode.MARKDOWN)

async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cid = u.effective_chat.id
    await try_react(c.bot, cid, u.message.message_id, "💡")
    st = await u.message.reply_text("🎲 Cooking up a brand new quiz...")
    topic = random.choice(["history", "animals", "tech", "space", "general"])
    p = f"Generate ONE quiz about {topic}. Respond ONLY with a clean raw JSON string without backticks. Format: {{\"question\": \"text?\", \"options\": [\"A\",\"B\",\"C\",\"D\"], \"correct_index\": 0}}"
    resp = await get_ai_response("You are a strict JSON generator.", p, "")
    await st.delete()
    try:
        clean = resp.replace("```json", "").replace("
```", "").strip()
        d = json.loads(clean)
        await c.bot.send_poll(chat_id=cid, question=f"🐱 Beluga's Quiz: {d['question']}", options=d['options'], type="quiz", correct_option_id=int(d['correct_index']), is_anonymous=False)
    except:
        await c.bot.send_poll(chat_id=cid, question="🐱 Beluga Quiz (Fallback): Best animal tongue?", options=["Dogs", "Cats/Lions", "Birds", "Frogs"], type="quiz", correct_option_id=1, is_anonymous=False)

async def monitor(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot: return
    uid, cid, now = u.effective_user.id, str(u.effective_chat.id), datetime.now()
    if uid not in spam_tracker: spam_tracker[uid] = []
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=2)]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= 4:
        try: await u.message.delete()
        except: pass
        return
    if cid not in db["seen"]: db["seen"][cid] = {}
    db["seen"][cid][str(uid)] = {"id": uid, "un": u.effective_user.username, "n": u.effective_user.first_name}
    db["counts"][cid] = db["counts"].get(cid, 0) + 1
    save_db()
    if db["counts"][cid] % 6 == 0:
        await try_react(c.bot, int(cid), u.message.message_id)
    text = (u.message.text or "").lower()
    is_reply = u.message.reply_to_message and u.message.reply_to_message.from_user and u.message.reply_to_message.from_user.id == c.bot.id
    if "beluga" in text or is_reply:
        await c.bot.send_chat_action(chat_id=int(cid), action="typing")
        emo = await ask_ai_for_emoji(u.message.text or "")
        await try_react(c.bot, int(cid), u.message.message_id, emo)
        resp = await get_ai_response(CHAT_PROMPT, u.message.text or "Hi!", "Meow... Slow connection!")
        await u.message.reply_text(resp)

async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    txt = (
        "```\n"
        "╔══════════════════════════════════════╗\n"
        "                🤖 BELUGA AI            \n"
        "╚══════════════════════════════════════╝\n"
        "
```\n"
        "💬 *Intelligent Telegram Chat Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 **User:**\n"
        "Hello Beluga\n\n"
        "🤖 **Beluga:**\n"
        "Hello! How can I help you today?\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ **Features:**\n"
        "• AI Chat Responses\n"
        "• Fast Reply System\n"
        "• Group Support\n"
        "• Clean Interface\n"
        "• 24/7 Active\n\n"
        "👋 *Type a message to begin...*"
    )
    await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    pass

async def main():
    token = os.environ.get("BOT_TOKEN")
    if not token: return
    app = TGApp.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("quiz", quiz_handler))
    app.add_handler(CommandHandler(["gay", "couple"], fun_dispatcher))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, monitor))
    app.add_error_handler(error_handler)
    await app.initialize()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
