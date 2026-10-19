"""Environment variables, logging, and tunable constants (timers, thresholds, sticker packs, shop catalogue)."""
import logging
import os
import re
import sys


# NOTE: image/video understanding uses a vision-capable AI model via API call
# (see _call_groq_vision) instead of local YOLO/torch — keeps this runnable
# on Render's free tier where heavy ML libraries won't fit in RAM.

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])


logger = logging.getLogger("Beluga")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
NVIDIA_KEY = os.environ.get("NVIDIA_API_KEY", "")


# Cloudflare needs two values, not one: a per-request API token AND the
# account id the Workers AI call is scoped to. Named to match Cloudflare's
# own dashboard terminology ("API Token" under My Profile > API Tokens, and
# "Account ID" on the account home page) so it's obvious which value goes
# where when you set them on Render.
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()


CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
HTTP_PORT = int(os.environ.get("PORT", "10000"))
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

# Optional search keys. With none of them set, /search and /bananalogic still work via
# DuckDuckGo + Bing + Wikipedia. Setting a key makes results noticeably more reliable.
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_CX = os.environ.get("GOOGLE_SEARCH_CX", "")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
KIDNAP_ME_URL = os.environ.get("KIDNAP_ME_URL", "https://t.me/BELUGAPY")
MONGO_URL = os.environ.get("MONGO_URL", "").strip()


if not BOT_TOKEN or len(BOT_TOKEN) < 20:
    logger.critical("BOT_TOKEN missing")
    sys.exit(1)


INACTIVITY_THRESHOLD_SEC = 4 * 3600
REVIVAL_COOLDOWN_SEC = 4 * 3600
WELCOME_AUTOEXPIRE_SEC = 180
REVIVAL_AUTOEXPIRE_SEC = 180
FILE_STICKERS = "beluga_stickers.json"
STICKER_PACK_MAIN = "t_me_belugapack_mystickers_by_fStikBot"
STICKER_PACK_SAFE = "t_me_staysafebelu_by_fStikBot"
STICKER_PACK_ALT = "pybelu_by_fStikBot"
STICKER_PACK_WELCOME = "BELUGA_WELCOMES_YOU_by_fStikBot"
PEACE_WINDOW_SEC = 180
PEACE_ALERT_COOLDOWN_SEC = 300
SINGLE_ABUSE_THRESHOLD = 3


PROFANITY_WORDS = {
    "madarchod", "behenchod", "bhosdike", "chutiya", "randi", "gandu", "lund",
    "chod", "harami", "kamina", "saala kutta", "fuck", "fucker", "bitch",
    "asshole", "bastard", "slut", "whore",
}


FLOOD_WINDOW_SEC = 3
FLOOD_COUNT = 4
RAID_WINDOW_SEC = 5
RAID_USER_THRESHOLD = 3
LOCK_COUNTDOWN_SEC = 10
LOCK_DURATION_SEC = 120


TAG_SHOP = [
    {"key": "CEO", "label": "CEO", "emoji": "👤", "cost": 7500, "color": "danger"},
    {"key": "MANAGER", "label": "MANAGER", "emoji": "👨‍💼", "cost": 7000, "color": "danger"},
    {"key": "ACTIVE", "label": "ACTIVE", "emoji": "😼", "cost": 4900, "color": "success"},
    {"key": "HELPER", "label": "HELPER", "emoji": "👷", "cost": 3700, "color": "success"},
]

# Custom Tag Cost & Timeout set correctly at global scope level
CUSTOM_TAG_COST = 20000
CUSTOM_TAG_TIMEOUT_SEC = 30 * 60


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\U0001F000-\U0001F0FF"
    "\uFE0F"
    "]", flags=re.UNICODE,
)


UPDATES_CHANNEL = "https://t.me/BELUGAPY"
START_VIDEO = "https://go.screenpal.com/watch/cO1oqenuAPr"
WIKI_UA = {"User-Agent": "BelugaBot/11.4"}
G_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "en-US,en;q=0.9"}
MONGO_CLEANUP_INTERVAL_SEC = 24 * 3600
MEMORY_STALE_DAYS = 14
MEDIA_MEMORY_SECONDS = 15 * 60
MEMORY_TTL_MESSAGES = 5  # after the stored list exceeds this many entries, it auto-clears

