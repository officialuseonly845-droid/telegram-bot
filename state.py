"""MongoDB handles and every in-memory dict/set that is shared between modules."""
import asyncio
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URL


mongo_client = AsyncIOMotorClient(MONGO_URL) if MONGO_URL else None
mongo_db = mongo_client["beluga_bot"] if mongo_client else None
mongo_memory_col = mongo_db["chat_memory"] if mongo_db is not None else None
mongo_leaderboard_col = mongo_db["leaderboard"] if mongo_db is not None else None
mongo_weekly_col = mongo_db["weekly_winners"] if mongo_db is not None else None
mongo_stickers_col = mongo_db["stickers"] if mongo_db is not None else None
mongo_tags_col = mongo_db["member_tags"] if mongo_db is not None else None
# Single-document settings store (holds the persistent /shutdown flag).
mongo_settings_col = mongo_db["bot_settings"] if mongo_db is not None else None


# Registry of every chat Beluga is in. Lives in its OWN
# collection, completely separate from mongo_memory_col — /clearmemory only
# ever touches mongo_memory_col, so this registry is NEVER wiped by it.
mongo_chats_col = mongo_db["known_chats"] if mongo_db is not None else None


# Lightweight name/username -> user_id lookup so the bot can "link" two
# people's chat memories when one asks about the other by name (see
# build_linked_context / find_user_by_mention below). Separate collection so
# /clearmemory (which only touches mongo_memory_col) never wipes it.
mongo_users_col = mongo_db["user_index"] if mongo_db is not None else None


known_chats = {}          # cid(str) -> {"id":int,"title":str,"username":str|None,"type":str}
last_welcome_msg = {}     # cid(str) -> message_id
last_group_activity = {}  # cid(str) -> datetime
last_revival_time = {}    # cid(str) -> datetime
recent_group_msgs = {}    # cid(str) -> deque[str] (rolling chat context for revival)
bot_status = {"running": False, "start_time": datetime.now(), "message_count": 0, "error_count": 0, "api_calls": 0, "failed_apis": 0, "username": ""}
away_mode_state = {"on": False}


# --- Peace mode (per-group, admin-toggled fight/abuse detection) ---
peace_mode_state = {}          # cid(str) -> {"on": bool}


recent_negative_events = {}    # cid(str) -> deque[(uid_str, name, datetime)]
last_peace_alert = {}          # cid(str) -> datetime
quiz_cooldown, active_polls, spam_tracker = {}, {}, {}
db = {"scores": {}, "weekly": {}, "seen": {}, "counts": {}}


# --- Anti-spam / anti-raid state ---
raid_flags = {}        # cid(str) -> {uid(str): last_flood_timestamp}


lockdown_state = {}     # cid(str) -> {"locking": bool, "active": bool, "prevent": bool}


# --- Daily stats (messages/day, mutes/bans, most active users, milestones) ---
daily_stats = {}  # cid(str) -> {"date": "YYYY-MM-DD", "messages": 0, "mutes": 0, "bans": 0, "user_counts": {uid: {"name":.., "count":0}}, "milestones_hit": set()}


# --- Tag shop (points -> cosmetic name tag, shown in leaderboard/stats — NOT an admin title) ---
member_tags = {}  # cid(str) -> {uid(str): tag_label}


# prompt_message_id(int) -> {"cid":str, "uid":str, "user_name":str, "fulfilled":bool}
pending_custom_tags = {}


fun_db = {"gay_couple_log": {}}
ttt_games, mine_games, user_in_game, game_timers, mine_timers, gm_tracker, gm_msg_lock = {}, {}, {}, {}, {}, {}, {}
mine_play_stats = {}
wm_sessions = {}
sticker_data = {"packs": {}, "banned_packs": []}
fun_cache_lock = asyncio.Lock()
exchange_cache = {}
cache_movers = {"ts": 0, "data": {}}
news_cache = {"crypto": {"ts": 0, "data": []}, "ai": {"ts": 0, "data": []}, "tech": {"ts": 0, "data": []}}


# --- Chat-aware media memory -------------------------------------------------
# Telegram's Bot API can't fetch old chat history, so Beluga remembers the last
# photo/video/sticker she SAW in each chat. That lets someone ask "what's in
# it?" a few messages later (no reply needed) and she still knows which media
# they mean instead of asking them to resend it.
last_chat_media: dict = {}   # cid(str) -> {"photo","video","sticker","uid","name","ts"}


# /shutdown switch. While "on" is True the bot ignores every update (see
# handlers/maintenance.py). Mirrored in MongoDB so it survives restarts and
# redeploys until the owner sends /restart.
maintenance_state = {"on": False, "since": None}
