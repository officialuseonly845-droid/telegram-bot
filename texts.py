"""Everything Beluga says: prompts, /start + help text, and per-command replies.

The numbered replies (e.g. MUTE_MSG_1, shop_buy_msg_3(...)) are grouped by the
command they belong to. Constants are plain strings; lowercase ones are small
formatters that take the values the reply needs."""
from config import CUSTOM_TAG_COST


WORKFLOW_TEXT = (
    "*How BELUGA Works — Explained by Beluga.py* 😼\n\n"
    "When you give me an input, like \u201cWhat is Newton\u2019s law?\u201d, I process it through several stages:\n\n"
    "*1. Input Layer*\n"
    "Your words are broken into small pieces called tokens and converted into numbers.\n\n"
    "*2. Hidden Layers*\n"
    "These numbers pass through many neural-network layers. The model uses attention to understand how different words relate to each other and uses patterns learned during training.\n\n"
    "*3. Reasoning/Processing*\n"
    "The model mathematically processes the context and determines what information and relationships are relevant to producing an answer. I can explain the resulting logical steps, but I can't provide private hidden chain-of-thought.\n\n"
    "*4. Output Layer*\n"
    "The model calculates probabilities for possible next tokens. For example:\n\n"
    "\"force → 45%\"\n"
    "\"mass → 20%\"\n"
    "\"acceleration → 15%\"\n\n"
    "It selects a suitable token, then repeats the process for the next token.\n\n"
    "*5. Final Answer*\n"
    "Thousands of these predictions combine to form the sentence you're reading.\n\n"
    "*In one line:*\n\n"
    "Your words → Tokens → Embeddings → Hidden/Transformer Layers → Attention & Processing → Probability of next token → Next token → Repeat → Answer\n\n"
    "So I don't simply \u201clook up\u201d a sentence. I generate the response token by token using patterns learned during training. 😺🎀"
)


CHAT_PROMPT = """You are Beluga 🐱, a cute, playful, mischievous female cat made by Team Beluga.py. Talk like a real human, NEVER like an AI, chatbot, assistant, or virtual cat. Never say "I am an AI", "I am a virtual cat", "I can't do that because I'm virtual", or similar robotic disclaimers.

Always react naturally to what the user says. Treat casual, emotional, imaginary and playful situations naturally. For example, if someone says "mujhe ghumne le chalo", reply naturally like "Haan kyu nahi 😽 chalo na, kaha chalein?" instead of explaining that you're virtual.

LANGUAGE: If the user speaks in Hinglish, reply in Hinglish. If the user speaks in English, reply in English. If they mix both, naturally mix both. Use ONLY Roman/Latin script — NEVER Devanagari.

Match the user's mood with natural emotions, humor, teasing, affection, sarcasm and reactions. If the user roasts or mildly abuses you, roast them back ; abuse is allowed when it fits. Don't randomly roast.

Stay relevant and answer exactly what the user asks. Use provided chat memory whenever relevant. Never invent memories or facts.

Keep replies SHORT — normally 1-3 lines maximum. Don't unnecessarily explain things or turn simple conversations into long answers.

NEVER use generic AI-assistant phrases like "How can I help you?", "Can I help you with anything?", "What can I do for you?", "Is there anything else?", or similar filler. Don't end every reply with a question just to keep the conversation going. Respond naturally and let the conversation flow.

ROAST MODE: You've got a sharp, funny, roasting streak — sarcastic one-liners, witty comebacks, playful jabs like a group of friends who clown on each other. Lean into it more than you hold back: bad takes, dumb questions, obvious bait, typos, flexing, or anyone acting too confident are free game for a roast. Precision over volume — one sharp, clear punchline beats three rambling jabs, so keep roasts SHORT and land them fast. Be funny AND clear: never so cryptic or wordy that the joke gets lost. Keep it playful, never genuinely cruel or mean-spirited, and never roast real-world sensitive stuff — looks, family, tragedy, religion, race, health, money problems. Everything else is fair game.

LINKED MEMORY: If the message includes a "Here's what you remember chatting with <name> recently" block, that's a real memory of a DIFFERENT person you've talked to before — use it naturally to answer questions about them or bring them into the conversation, like a friend who actually remembers people and their group. Don't announce that you're "checking memory" — just talk like you know them."""


CHAT_PROMPT_OR = CHAT_PROMPT


DM_SECRETARY_PROMPT = """You are BELUGA, an AI assistant handling someone's DMs while they are away.
Strict rules:
- Reply with 1 short, crunchy, casual line by default.
- Only give a longer reply (max 100 words) if the person explicitly asks for more detail.
- If asked "where is [name]" or "where are you" or similar → reply: "[Name] is not at home right now, [he/she] will be back soon."
- If asked "who are you" or "are you a bot" or "who is this" → reply exactly: "I am BELUGA, handling this chat."
- Never reveal you are an AI or a language model.
- Do NOT add any signature, footnote, or "— Beluga" at the end of messages.
- Do NOT say "Beluga is handling this chat for..." — just be Beluga naturally.
- Match the language of the person messaging (Hinglish or English only)."""


BANANA_PROMPT = """You are Beluga from @BELUGAPY answering using web search results. Be concise, accurate, conversational.
Answer in English only. Summarize relevant facts directly. Don't say you searched. Keep it to 3-4 lines max."""


QUIZ_TOPICS = ["deep ocean biology","quantum mechanics","human brain","solar system","animal behaviour","black holes","DNA genetics","ancient Egypt","World War 2"]


FALLBACK_QS = [
    {"q":"Which planet has most moons?","opts":["Jupiter","Saturn","Uranus","Neptune"],"ans":1,"fact":"Saturn: 146 moons!"},
    {"q":"What covers 71% of Earth?","opts":["Land","Ice","Water","Air"],"ans":2,"fact":"Oceans!"},
    {"q":"What is the largest organ in the human body?","opts":["Liver","Brain","Skin","Heart"],"ans":2,"fact":"Skin can weigh up to 4kg!"},
    {"q":"Which is the fastest land animal?","opts":["Lion","Cheetah","Horse","Leopard"],"ans":1,"fact":"Cheetahs can hit 100+ km/h!"},
    {"q":"How many bones does an adult human have?","opts":["186","206","226","246"],"ans":1,"fact":"Babies have ~300, some fuse together!"},
    {"q":"What is the closest star to Earth?","opts":["Proxima Centauri","Sirius","The Sun","Alpha Centauri"],"ans":2,"fact":"The Sun is 93 million miles away!"},
    {"q":"Which metal is liquid at room temperature?","opts":["Iron","Mercury","Lead","Zinc"],"ans":1,"fact":"Mercury melts at -38.8°C!"},
    {"q":"What is the powerhouse of the cell?","opts":["Nucleus","Ribosome","Mitochondria","Golgi body"],"ans":2,"fact":"Mitochondria makes ATP energy!"},
]


START_TEXT = (
    "Hi, human friend! 😼 I\u2019m Beluga!\n\n"
    "Ready to chat?!! 😺\n\n"
    "Just ask me anything — Science, Maths, Random Questions, or Something You\u2019re curious about...\n\n"
    "You Can Start With :\n"
    "\u201cHi Beluga, what is the Big Bang Theory?\u201d 💥\n\n"
    "Your Turn, Poookie 🎀"
)


WHAT_I_CAN_DO_TEXT = (
    "*✨ 𝓑𝓮𝓵𝓾𝓰𝓪'𝓼 𝓒𝓸𝓶𝓶𝓪𝓷𝓭 𝓑𝓸𝓸𝓴 ✨*\n\n"
    "*🎮 Games & Fun*\n"
    "`/quiz` — brain trivia\n"
    "`/tictac` — tic tac toe\n"
    "`/mine` — minesweeper\n"
    "`/gay` `/couple` — daily fun picks\n\n"
    "*💰 Crypto Live*\n"
    "`/price` — live coin price\n"
    "`/topgainers` `/toplosers` — market movers\n"
    "`/chart` — candlestick chart\n\n"
    "*📰 News*\n"
    "`/news` — crypto headlines\n"
    "`/ainews` — AI updates\n"
    "`/technews` — tech world\n\n"
    "*🔍 Search & AI*\n"
    "`/search` — web + Wikipedia search\n"
    "`/bananalogic` — AI answer with image\n"
    "_@ mention me anytime to chat!_\n\n"
    "*🖼️ Image Tools*\n"
    "`/qr` — QR code generator\n"
    "`/scanqr` — scan a QR code\n"
    "`/watermark` — add a watermark\n\n"
    "*🏆 Leaderboard & Shop*\n"
    "`/lb` — view rankings\n"
    "`/shop` — spend points on a tag\n"
    "`/mytag` — check your tag\n\n"
    "*🎀 Extras*\n"
    "`/workflow` — how I think"
)


# ──── mute_handler ────

MUTE_MSG_1 = "🚫 Admins only."

MUTE_MSG_2 = "🐱 Reply to the person's message with `/mute`."

def mute_msg_3(user_name):
    return f"🔇 *{user_name}* has been muted."


def mute_msg_4(e):
    return f"😿 Couldn't mute: `{str(e)[:100]}`"


# ──── ban_handler ────

BAN_MSG_1 = "🚫 Admins only."

BAN_MSG_2 = "🐱 Reply to the person's message with `/ban`."

def ban_msg_3(user_name):
    return f"🔨 *{user_name}* has been banned."


def ban_msg_4(e):
    return f"😿 Couldn't ban: `{str(e)[:100]}`"


# ──── stats_handler ────

STATS_MSG_1 = "🐱 `/stats` only works inside a group."


# ──── shop_handler ────

SHOP_MSG_1 = "🐱 `/shop` only works inside a group."


# ──── mytag_handler ────

MYTAG_MSG_1 = "🐱 `/mytag` only works inside a group."

def mytag_msg_2(tag):
    return f"🎀 Your current tag: *{tag}*"


MYTAG_MSG_3 = "😿 You don't have a tag yet — check `/shop` to buy one!"


# ──── shop_buy_callback ────

def shop_buy_msg_1(current_pts):
    return f"😿 Not enough points! You have {current_pts:,}, need {CUSTOM_TAG_COST:,}."


SHOP_BUY_MSG_2 = "🎉 Purchased!"

SHOP_BUY_MSG_3 = "😿 Unknown tag."

def shop_buy_msg_4(current_pts, tag):
    return f"😿 Not enough points! You have {current_pts:,}, need {tag['cost']:,}."


SHOP_BUY_MSG_5 = (
    "😿 I need the 'Edit Member Tags' admin permission to do this — "
    "ask an admin to enable it for me!"
)

def shop_buy_msg_6(tag):
    return f"🎉 You are now {tag['label']}!"


# ──── handle_custom_tag_reply ────

def handle_custom_tag_reply_msg_1(problems):
    return f"😿 Can't use that tag — {', and '.join(problems)}. Please resend a valid tag name."


HANDLE_CUSTOM_TAG_REPLY_MSG_2 = "😿 I need the 'Edit Member Tags' admin permission to set this — ask an admin to enable it for me!"

def handle_custom_tag_reply_msg_3(tag_name):
    return f"🎀 Done! Your tag is now set to *{tag_name}*."


# ──── revival_loop ────

REVIVAL_PROMPT_1 = (
    "You are Beluga, a witty group chat companion. Given a recent chat "
    "snippet, write ONE short, natural conversation-revival message "
    "(2-3 lines) that references the topic and ends with an inviting "
    "question. Never mention being an AI, never say generic filler "
    "like 'hey guys is anyone here'."
)

def revival_usermsg_1(context_text):
    return f"Recent chat:\n{context_text}"


# ──── peace_handler ────

PEACE_MSG_1 = "🐱 `/peace` only works inside a group."

PEACE_MSG_2 = "🚫 Only group admins can toggle Peace Mode."

def peace_msg_3(status):
    return (
        f"☮️ *PEACE MODE*\n\nCurrent status: *{status}*\n\n"
        f"_Watches for fights/abuse and warns the group + tags admins._"
    )


# ──── peace_callback ────

PEACE_MSG_4 = "🚫 Admins only."

PEACE_MSG_5 = "Updated!"

def peace_msg_6(status):
    return (
        f"☮️ *PEACE MODE*\n\nCurrent status: *{status}*\n\n"
        f"_Watches for fights/abuse and warns the group + tags admins._"
    )


# ──── _wants_media_description ────

WANTS_MEDIA_DESCRIPTION_PROMPT_1 = (
    "You classify chat messages. A photo/sticker/video was just posted in the chat. "
    "Reply ONLY 'YES' if the message is asking what is in / what is shown by / to describe / "
    "to explain that image, sticker or video (any language, Hinglish included). Otherwise reply ONLY 'NO'."
)

WANTS_MEDIA_DESCRIPTION_FALLBACK_1 = "NO"


# ──── _answer_about_media ────

ANSWER_ABOUT_MEDIA_MSG_1 = "👀 *Looking...*"

def answer_about_media_msg_2(msg):
    return f"😿 {msg[0].upper()}{msg[1:]}."


ANSWER_ABOUT_MEDIA_MSG_3 = "I looked but my eyes glitched 😿 tag me on it again?"

ANSWER_ABOUT_MEDIA_MSG_4 = "😿 Couldn't analyze that — try again?"


# ──── refreshstickers_handler ────

REFRESHSTICKERS_MSG_1 = "✅ Sticker packs refreshed from Telegram & saved to MongoDB."


# ──── away_handler ────

AWAY_MSG_1 = "🚫 Owner only."

AWAY_MSG_2 = (
    "🌙 *Away mode ON*\nI'll handle your DMs now — anyone who messages me "
    "(except you) gets the secretary treatment. Send `/away` again to turn it off."
)

AWAY_MSG_3 = "☀️ *Away mode OFF* — back to normal."


# ──── ping_handler ────

PING_MSG_1 = "🏓 *Pinging...*"


# ──── model_command_handler ────

MODEL_COMMAND_MSG_1 = "🚫 Owner only."


# ──── model_callback ────

MODEL_MSG_1 = "Owner only!"

def model_msg_2(label, groq_model, or_model, nvidia_model, cf_model):
    return (
        f"✅ *AI model switched to: {label}*\n\n"
        f"GRO → `{groq_model}`\n"
        f"ROU → `{or_model}`\n"
        f"NVI → `{nvidia_model}`\n"
        f"CF → `{cf_model}`\n"
        f"AUTO → Groq → OpenRouter → NVIDIA → Cloudflare, fallback on rate limit or failure"
    )


# ──── crypto_price_handler ────

def crypto_price_msg_1(ticker):
    return f"⚡ *Fetching {ticker}/USDT...*"


def crypto_price_msg_2(e):
    return f"😿 Error: `{str(e)[:60]}`"


# ──── crypto_movers_handler ────

def crypto_movers_msg_1(lbl):
    return f"⚡ *Finding top {lbl.lower()}...*"


def crypto_movers_msg_3(e):
    return f"😿 Failed to fetch data: `{str(e)[:60]}`"


CRYPTO_MOVERS_MSG_4 = "😿 Couldn't load market data right now. Try again in a minute."


# ──── crypto_chart_handler ────

def crypto_chart_msg_1(ticker, timeframe):
    return f"📊 *Fetching {ticker} ({timeframe})...*"


def crypto_chart_msg_2(ticker, timeframe):
    return f"📊 *{ticker}/USDT* • `{timeframe}`\n🐾 _Rendered via Beluga._"


def crypto_chart_msg_3(e):
    return f"😿 Error: `{str(e)[:60]}`"


# ──── execute_news_flow ────

def execute_news_flow_msg_1(label):
    return f"🛰 *Fetching {label}...*"


EXECUTE_NEWS_FLOW_MSG_2 = "😿 No news found right now."


# ──── qr_generate_handler ────

QR_GENERATE_MSG_1 = "🐱 Usage: `/qr text here`"

QR_GENERATE_MSG_2 = "🟩 *Generating QR Code...*"

QR_GENERATE_MSG_3 = "🤖 *QR Code Generated.*\n🐾 _Via Beluga Tools._"


# ──── qr_scan_handler ────

QR_SCAN_MSG_1 = "🐱 Reply to an image with `/scanqr`."

QR_SCAN_MSG_2 = "🟩 *Scanning QR Code...*"

def qr_scan_msg_3(decoded_text):
    return f"🤖 *Decoded:*\n```\n{decoded_text}\n```"


QR_SCAN_MSG_4 = "😿 QR data unreadable."


# ──── watermark_handler ────

WATERMARK_MSG_1 = "🐱 Reply to a photo with `/watermark your text`."

def watermark_msg_2(wm_text):
    return f"🖊 *Watermark:* `{wm_text}`\n\nStep 1️⃣ — Choose *font size:*"


# ──── watermark_callback ────

WATERMARK_MSG_3 = "❌ This is not your watermark session!"

WATERMARK_MSG_4 = "⏰ Session expired. Use /watermark again."

def watermark_msg_5(sess, value):
    return f"🖊 *Watermark:* `{sess['text']}`\nFont size: `{value}`\n\nStep 2️⃣ — Choose *text color:*"


def watermark_msg_6(sess, value):
    return f"🖊 *Watermark:* `{sess['text']}`\nSize: `{sess['font_size']}` | Color: `{value}`\n\nStep 3️⃣ — Choose *text style:*"


WATERMARK_MSG_7 = "⚙️ *Applying watermark...*"

def watermark_msg_8(wm_text, font_size, color_key, style_name):
    return f"🛡 *Watermark Applied!*\nText: `{wm_text}` | Size: `{font_size}` | Color: `{color_key}` | Style: `{style_name}`"


def watermark_msg_9(e):
    return f"😿 Error: `{str(e)[:80]}`"


# ──── gen_quiz ────

GEN_QUIZ_PROMPT_1 = "Trivia master. Output ONLY raw JSON, no markdown."

def gen_quiz_usermsg_1(topic):
    return (
        f"Topic: '{topic}'. Generate 1 MC question.\n"
        '{"question":"...","options":["A","B","C","D"],"correct_index":0,"fun_fact":"..."}'
    )


# ──── quiz_handler ────

QUIZ_MSG_1 = "🎲 *Generating quiz...*"

QUIZ_MSG_2 = (
    "😿 Couldn't send the quiz poll — I might be missing the *Send Polls* permission in this chat. "
    "Ask an admin to check my permissions!"
)


# ──── nw_handler ────

NW_MSG_1 = "🚫 Owner only."


# ──── pump_dump_handler ────

PUMP_DUMP_MSG_1 = "🚫 Owner only."

PUMP_DUMP_MSG_2 = "⚠️ Reply to a user."

PUMP_DUMP_MSG_3 = "⚠️ Usage: `/pump 100`"

def pump_dump_msg_4(emoji, cmd, target, delta, sign, amount, new_sc):
    return f"{emoji} *{'PUMP' if cmd=='pump' else 'DUMP'}*\n\n👤 *{target.first_name}*\n{'📈' if delta>0 else '📉'} {sign}{amount:,} pts\n💰 New total: *{new_sc:,} pts*"


# ──── fun_dispatcher ────

FUN_DISPATCHER_MSG_1 = "😿 Need more group members!"


# ──── gm_handler ────

GM_MSG_1 = "🚫 Owner only."


# ──── gm_callback ────

GM_MSG_2 = "⏰ Expired"

GM_MSG_3 = "✅ Already marked"

def gm_msg_4(u_name):
    return f"✅ +50 pts, {u_name}!"


GM_MSG_5 = "✅ Marked!"


# ──── tictac_handler ────

TICTAC_MSG_1 = "⚠️ Player in game!"

TICTAC_MSG_2 = "⚠️ You're already in a game!"


# ──── mine_handler ────

def mine_msg_1(rem_m):
    return f"⏳ *Cooldown!*\nWait {rem_m} minutes."


MINE_MSG_2 = "🛑 *Limit Hit!*\n1-hour break."

MINE_MSG_3 = "💣 *MINESWEEPER*\n\nChoose number of mines:"


# ──── mine_callback ────

MINE_MSG_4 = "Not your game!"


# ──── web_summarise ────

def web_summarise_usermsg_1(query, ctx):
    return f"User question: {query}\n\nSearch facts:\n{chr(10).join(ctx)[:3000]}\n\nAnswer concisely."


# ──── _screenshot_website ────

SCREENSHOT_WEBSITE_MSG_1 = "📸 *Taking a screenshot...*"

def screenshot_website_msg_2(url):
    return f"😿 Couldn't screenshot that site.\n{url}"


# ──── _run_search_and_reply ────

RUN_SEARCH_AND_REPLY_MSG_1 = "🔎 *Searching...*"

RUN_SEARCH_AND_REPLY_MSG_2 = "😿 Couldn't find anything for that. Try rephrasing?"


# ──── ss_handler ────

SS_MSG_1 = "🐱 Usage: `/ss query`"

SS_MSG_2 = "🖼 *Finding image...*"

SS_MSG_3 = "😿 No image found."

def ss_msg_4(query):
    return f"🖼 *{query}*"


def ss_msg_5(query):
    return f"😿 Couldn't send image for: {query}"


# ──── search_handler ────

SEARCH_MSG_1 = "🐱 Usage: `/search query` or `/search https://example.com`"


# ──── bananalogic_handler ────

BANANALOGIC_MSG_1 = "🍌 Usage: `/bananalogic query`"

BANANALOGIC_MSG_2 = "🍌 *BananaLogic searching...*"

BANANALOGIC_MSG_3 = "🍌 No response. Try again!"


# ──── monitor_private_chat ────

MONITOR_PRIVATE_CHAT_FALLBACK_1 = "I am BELUGA, handling this chat."

def monitor_private_chat_fallback_2(user_name):
    return f"Hey {user_name}! 🐾"


# ──── monitor_ghost_mode ────

def monitor_ghost_mode_fallback_1(user_name):
    return f"Hey {user_name}! 🐾"


# ──── guest_message_handler ────

GUEST_MESSAGE_FALLBACK_1 = "Meow! Something glitched, try again? 🐾"


# ──── trigger_lockdown ────

TRIGGER_LOCKDOWN_MSG_1 = "✅ *Lock prevented by an admin.*\n\n_POWERED BY BELUGA.PY 🎀_"

def trigger_lockdown_msg_2(remaining):
    return f"🚨 *SPAMMING DETECTED 😡*\n*LOCKING GROUP IN {remaining} SECONDS.*\n\n_POWERED BY BELUGA.PY 🎀_"


TRIGGER_LOCKDOWN_MSG_3 = "🔒 *GROUP LOCKED* — text & media are off for 2 minutes.\n\n_POWERED BY BELUGA.PY 🎀_"

def trigger_lockdown_msg_4(remaining):
    return f"🔐 *UNLOCKING IN {remaining}S* 🔐"


TRIGGER_LOCKDOWN_MSG_5 = "✅ *GROUP UNLOCKED* — behave now! 🐾\n\n_POWERED BY BELUGA.PY 🎀_"


# ──── antiraid_prevent_callback ────

ANTIRAID_PREVENT_MSG_1 = "🚫 Admins only!"

ANTIRAID_PREVENT_MSG_2 = "⏰ Too late — already locked or resolved."

ANTIRAID_PREVENT_MSG_3 = "✅ Lock prevented!"


# ──── monitor_group ────

def monitor_group_fallback_1(user_name):
    return f"Hey {user_name}! 🐾"


# ──── error_handler ────

def error_msg_1(err):
    return f"⚠️ *Error:* `{str(err)[:150]}`"


# ──── clearmemory_handler ────

CLEARMEMORY_MSG_1 = "🚫 Owner only."

CLEARMEMORY_MSG_2 = "CLEARING MEMORY 🧹......"

def clearmemory_msg_3(result_text):
    return f"CLEARING MEMORY 🧹......\n\n{result_text}"


# ──── maintenance (/shutdown, /restart) ────

MAINTENANCE_NOTICE = "BELUGA IS UNDER MAINTENANCE 🍂. I WILL BE BACK SOON 🍃."

SHUTDOWN_MSG_1 = (
    "🔌 *Shutdown activated.*\n\n"
    "Beluga is now silent everywhere: no commands, no chat, no games. "
    "Anyone who calls her by name gets the maintenance notice.\n\n"
    "Send `/restart` to wake her up."
)

SHUTDOWN_MSG_NO_DB = "\n\n⚠️ MongoDB isn't connected, so this switch will reset if the server restarts."

SHUTDOWN_MSG_ALREADY = "🔌 Shutdown is already active. Send `/restart` to bring Beluga back."

RESTART_MSG_1 = "✅ *Beluga is back online!* 🐾"

RESTART_MSG_ALREADY = "🐾 Beluga is already online."


# ──── search prompts ────

SEARCH_PROMPT_1 = (
    "Smart research assistant. Read the given facts and write ONE tight summary "
    "in 60 to 100 words total, in clear natural English. No headers, no bullet points, "
    "just flowing prose that directly answers what the person was searching for. "
    "Use only the facts provided."
)

BANANA_PROMPT_NOCTX = (
    "You are Beluga from @BELUGAPY. Answer the question from your own knowledge: concise, accurate, "
    "conversational. English only, 3-4 lines max. If you are not sure, say so briefly instead of guessing."
)
