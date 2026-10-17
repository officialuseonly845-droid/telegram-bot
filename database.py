"""MongoDB persistence: leaderboards, stickers, tags, per-user chat memory and the /shutdown flag."""
import asyncio
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from config import (
    MEMORY_STALE_DAYS,
    MEMORY_TTL_MESSAGES,
    MONGO_CLEANUP_INTERVAL_SEC,
    logger,
)
from state import (
    db,
    known_chats,
    member_tags,
    mongo_chats_col,
    mongo_leaderboard_col,
    maintenance_state,
    mongo_memory_col,
    mongo_settings_col,
    mongo_stickers_col,
    mongo_tags_col,
    mongo_users_col,
    mongo_weekly_col,
    sticker_data,
)


async def sync_tags_to_mongo(cid: str):
    if mongo_tags_col is None:
        return
    try:
        await mongo_tags_col.replace_one(
            {"_id": cid}, {"_id": cid, "tags": member_tags.get(cid, {})}, upsert=True
        )
    except Exception as e:
        logger.error(f"[mongo] sync_tags_to_mongo({cid}) failed: {e}")


async def load_persistent_data():
    """Runs once at startup. Leaderboard, weekly winners, and stickers all load from MongoDB."""
    if mongo_leaderboard_col is not None:
        try:
            db["scores"] = {}
            async for doc in mongo_leaderboard_col.find({}):
                db["scores"][doc["_id"]] = doc.get("users", {})
            logger.info(f"[mongo] Leaderboard loaded ({len(db['scores'])} chats)")
        except Exception as e:
            logger.error(f"[mongo] Leaderboard load failed: {e}")
            db["scores"] = {}
    else:
        db["scores"] = {}
        logger.warning("[mongo] MONGO_URL not set — leaderboard will not persist.")

    if mongo_weekly_col is not None:
        try:
            db["weekly"] = {}
            async for doc in mongo_weekly_col.find({}):
                db["weekly"][doc["_id"]] = doc.get("data", {})
        except Exception as e:
            logger.error(f"[mongo] Weekly winners load failed: {e}")
            db["weekly"] = {}
    else:
        db["weekly"] = {}

    if mongo_stickers_col is not None:
        try:
            packs, banned = {}, []
            async for doc in mongo_stickers_col.find({}):
                if doc["_id"] == "_banned_packs":
                    banned = doc.get("banned_packs", [])
                else:
                    packs[doc["_id"]] = doc.get("file_ids", [])
            sticker_data["packs"] = packs
            sticker_data["banned_packs"] = banned
            logger.info(f"[mongo] Stickers loaded ({len(packs)} packs, {len(banned)} banned)")
        except Exception as e:
            logger.error(f"[mongo] Sticker load failed: {e}")
            sticker_data.clear()
            sticker_data.update({"packs": {}, "banned_packs": []})
    else:
        sticker_data.clear()
        sticker_data.update({"packs": {}, "banned_packs": []})
        logger.warning("[mongo] MONGO_URL not set — stickers will not persist.")

    if mongo_tags_col is not None:
        try:
            member_tags.clear()
            async for doc in mongo_tags_col.find({}):
                member_tags[doc["_id"]] = doc.get("tags", {})
            logger.info(f"[mongo] Member tags loaded ({len(member_tags)} chats)")
        except Exception as e:
            logger.error(f"[mongo] Member tags load failed: {e}")
            member_tags.clear()
    else:
        member_tags.clear()

    known_chats.clear()
    if mongo_chats_col is not None:
        try:
            async for doc in mongo_chats_col.find({}):
                known_chats[doc["_id"]] = {
                    "id": doc["id"], "title": doc.get("title", ""),
                    "username": doc.get("username"), "type": doc.get("type", ""),
                }
            logger.info(f"[mongo] Known chats loaded ({len(known_chats)} chats)")
        except Exception as e:
            logger.error(f"[mongo] Known chats load failed: {e}")

    await load_maintenance_state()


async def load_maintenance_state() -> None:
    """Restores the /shutdown flag from MongoDB. Retries a few times so a brief
    Mongo hiccup at boot can't silently wake a bot the owner shut down."""
    maintenance_state["on"] = False
    maintenance_state["since"] = None
    if mongo_settings_col is None:
        return
    for attempt in range(1, 4):
        try:
            doc = await mongo_settings_col.find_one({"_id": "maintenance"})
            if doc and doc.get("on"):
                maintenance_state["on"] = True
                maintenance_state["since"] = doc.get("since")
                logger.warning("[maintenance] Shutdown flag is ON. Bot stays silent until /restart.")
            return
        except Exception as e:
            logger.error(f"[maintenance] load attempt {attempt}/3 failed: {e}")
            await asyncio.sleep(2)


async def save_maintenance_state(on: bool) -> bool:
    """Persists the /shutdown flag. Returns True only if it reached MongoDB."""
    if mongo_settings_col is None:
        return False
    try:
        await mongo_settings_col.replace_one(
            {"_id": "maintenance"},
            {"_id": "maintenance", "on": bool(on), "since": maintenance_state.get("since"),
             "updated": datetime.now(timezone.utc).isoformat()},
            upsert=True,
        )
        return True
    except Exception as e:
        logger.error(f"[maintenance] save failed: {e}")
        return False


async def register_chat(chat) -> None:
    """Adds/updates a chat in the persistent registry of chats Beluga is in."""
    if chat is None or chat.type == "private":
        return
    cid = str(chat.id)
    entry = {"id": chat.id, "title": chat.title or "", "username": chat.username, "type": chat.type}
    if known_chats.get(cid) == entry:
        return
    known_chats[cid] = entry
    if mongo_chats_col is not None:
        try:
            await mongo_chats_col.replace_one({"_id": cid}, {"_id": cid, **entry}, upsert=True)
        except Exception as e:
            logger.error(f"[mongo register_chat] {e}")


async def unregister_chat(chat_id) -> None:
    known_chats.pop(str(chat_id), None)
    if mongo_chats_col is not None:
        try:
            await mongo_chats_col.delete_one({"_id": str(chat_id)})
        except Exception as e:
            logger.error(f"[mongo unregister_chat] {e}")


async def sync_leaderboard_chat_to_mongo(cid: str):
    """Push one chat's current scores dict to MongoDB immediately."""
    if mongo_leaderboard_col is None:
        return
    try:
        await mongo_leaderboard_col.replace_one(
            {"_id": cid}, {"_id": cid, "users": db.get("scores", {}).get(cid, {})}, upsert=True
        )
    except Exception as e:
        logger.error(f"[mongo] sync_leaderboard_chat_to_mongo({cid}) failed: {e}")


async def sync_weekly_to_mongo(cid: str):
    """Push one chat's weekly-winners record to MongoDB immediately."""
    if mongo_weekly_col is None:
        return
    try:
        await mongo_weekly_col.replace_one(
            {"_id": cid}, {"_id": cid, "data": db.get("weekly", {}).get(cid, {})}, upsert=True
        )
    except Exception as e:
        logger.error(f"[mongo] sync_weekly_to_mongo({cid}) failed: {e}")


async def sync_sticker_pack_to_mongo(pack_name: str):
    """Push one sticker pack's file_ids to MongoDB."""
    if mongo_stickers_col is None:
        return
    try:
        await mongo_stickers_col.replace_one(
            {"_id": pack_name}, {"_id": pack_name, "file_ids": sticker_data["packs"].get(pack_name, [])}, upsert=True
        )
    except Exception as e:
        logger.error(f"[mongo] sync_sticker_pack_to_mongo({pack_name}) failed: {e}")


async def sync_banned_packs_to_mongo():
    """Push the banned-packs list to MongoDB."""
    if mongo_stickers_col is None:
        return
    try:
        await mongo_stickers_col.replace_one(
            {"_id": "_banned_packs"}, {"_id": "_banned_packs", "banned_packs": sticker_data["banned_packs"]}, upsert=True
        )
    except Exception as e:
        logger.error(f"[mongo] sync_banned_packs_to_mongo failed: {e}")


async def save_all_data():
    """No-op now — all writes (leaderboard, weekly, stickers) sync to MongoDB immediately at the point of change."""
    pass


async def periodic_sync():
    """Kept as a no-op background loop for structural compatibility; all data now syncs immediately."""
    while True:
        await asyncio.sleep(30)


async def load_sticker_pack(bot, pack_name: str):
    """Fetch a sticker pack's file_ids from Telegram, store in memory + MongoDB."""
    try:
        sticker_set = await bot.get_sticker_set(pack_name)
        file_ids = [s.file_id for s in sticker_set.stickers]
        sticker_data["packs"][pack_name] = file_ids
        await sync_sticker_pack_to_mongo(pack_name)
        logger.info(f"Sticker pack loaded: {pack_name} ({len(file_ids)} stickers)")
    except Exception as e:
        logger.warning(f"Could not load sticker pack '{pack_name}': {e}")


async def ban_sticker_pack(pack_name: str):
    """Add a pack to the banned list. Any sticker sent FROM this pack by any
    user in any group will be auto-deleted by the bot (see monitor())."""
    if pack_name not in sticker_data["banned_packs"]:
        sticker_data["banned_packs"].append(pack_name)
        await sync_banned_packs_to_mongo()
        logger.info(f"Sticker pack banned: {pack_name}")


def is_pack_banned(pack_name: Optional[str]) -> bool:
    """Check whether a sticker's set_name is on the banned list."""
    if not pack_name:
        return False
    return pack_name in sticker_data.get("banned_packs", [])


async def get_random_sticker_from(pack_name: str) -> Optional[str]:
    """Get a random sticker file_id from ONE specific pack (skips if banned)."""
    if is_pack_banned(pack_name):
        return None
    stickers = sticker_data.get("packs", {}).get(pack_name, [])
    return random.choice(stickers) if stickers else None


async def get_random_sticker_any() -> Optional[str]:
    """Get a random sticker from ANY loaded, non-banned pack."""
    pool = []
    for pack_name, stickers in sticker_data.get("packs", {}).items():
        if pack_name not in sticker_data.get("banned_packs", []):
            pool.extend(stickers)
    return random.choice(pool) if pool else None


async def mongo_cleanup_loop():
    """
    Periodically frees Mongo space — but ONLY prunes stale, non-critical
    data: per-user chat memory (mongo_memory_col) that's empty or hasn't
    been touched in MEMORY_STALE_DAYS. This collection is already a rolling
    6-message window that resets constantly, so old/abandoned entries carry
    no real value.
    NEVER touches: known_chats (the /broadcast registry), leaderboard,
    weekly_winners, member_tags, or stickers — anything
    that represents real state or history the bot actually needs.
    """
    while True:
        await asyncio.sleep(MONGO_CLEANUP_INTERVAL_SEC)
        if mongo_memory_col is None:
            continue
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=MEMORY_STALE_DAYS)
            deleted = 0
            async for doc in mongo_memory_col.find({}):
                messages = doc.get("messages", [])
                if not messages:
                    await mongo_memory_col.delete_one({"_id": doc["_id"]})
                    deleted += 1
                    continue
                try:
                    last_ts = datetime.fromisoformat(messages[-1]["ts"])
                except Exception:
                    continue
                if last_ts < cutoff:
                    await mongo_memory_col.delete_one({"_id": doc["_id"]})
                    deleted += 1
            if deleted:
                logger.info(f"[mongo_cleanup] Freed space — removed {deleted} stale/empty chat-memory docs")
        except Exception as e:
            logger.error(f"[mongo_cleanup_loop] {e}")


def _mongo_available() -> bool:
    return mongo_memory_col is not None


async def get_user_memory(user_id) -> dict:
    """Fetch a user's rolling chat memory from MongoDB. Returns {"messages": []} if none/unavailable."""
    if not _mongo_available():
        return {"messages": []}
    try:
        doc = await mongo_memory_col.find_one({"_id": str(user_id)})
        return doc if doc else {"messages": []}
    except Exception as e:
        logger.error(f"[mongo get_user_memory] {e}")
        return {"messages": []}


async def save_user_memory(user_id, memory_data: dict) -> bool:
    """Overwrite a user's full memory doc in MongoDB."""
    if not _mongo_available():
        return False
    try:
        await mongo_memory_col.replace_one({"_id": str(user_id)}, memory_data, upsert=True)
        return True
    except Exception as e:
        logger.error(f"[mongo save_user_memory] {e}")
        return False


async def append_chat_history(user_id, user_text: str, bot_reply: str, name: str = "", username: str = "") -> bool:
    """
    Append one (user, bot) exchange to a user's rolling MEMORY_TTL_MESSAGES-message
    memory. Once the count exceeds that limit, the entire history for that
    user is auto-cleared (per spec) so the next message starts fresh.
    Also stores the person's display name + Telegram @username on the same
    doc, so the memory is always tied to WHO it belongs to, not just their id.
    """
    if not _mongo_available():
        return False
    try:
        doc = await mongo_memory_col.find_one({"_id": str(user_id)}) or {"messages": []}
        messages = doc.get("messages", [])
        messages.append({"role": "user", "text": user_text[:500], "ts": datetime.now(timezone.utc).isoformat()})
        messages.append({"role": "bot", "text": bot_reply[:500], "ts": datetime.now(timezone.utc).isoformat()})
        if len(messages) > MEMORY_TTL_MESSAGES:
            messages = []
        update_doc = {"_id": str(user_id), "messages": messages}
        if name:
            update_doc["name"] = name
        elif doc.get("name"):
            update_doc["name"] = doc["name"]
        if username:
            update_doc["username"] = username
        elif doc.get("username"):
            update_doc["username"] = doc["username"]
        await mongo_memory_col.replace_one({"_id": str(user_id)}, update_doc, upsert=True)
        return True
    except Exception as e:
        logger.error(f"[mongo append_chat_history] {e}")
        return False


def build_chat_history_context(memory: dict) -> str:
    """Turn stored rolling messages into a short context block for the AI prompt."""
    messages = memory.get("messages", [])
    if not messages:
        return ""
    lines = []
    for m in messages[-MEMORY_TTL_MESSAGES:]:
        who = "User" if m.get("role") == "user" else "You (Beluga)"
        lines.append(f"{who}: {m.get('text', '')}")
    return "\n\nRecent conversation:\n" + "\n".join(lines)


def build_memory_context(memory: dict) -> str:
    """Kept for compatibility with callers; rolling history covers this now."""
    return ""


async def update_user_index(user_id, name: str, username: str):
    """
    Keeps a small name/username -> user_id lookup up to date every time
    someone chats, so find_user_by_mention() can resolve "what's up with
    <name>" style questions to a real stored memory. Separate from
    mongo_memory_col so /clearmemory never touches it.
    """
    if mongo_users_col is None:
        return
    try:
        fields = {}
        if name:
            fields["name"] = name
        if username:
            fields["username"] = username
        if not fields:
            return
        await mongo_users_col.update_one({"_id": str(user_id)}, {"$set": fields}, upsert=True)
    except Exception as e:
        logger.error(f"[mongo update_user_index] {e}")


async def find_user_by_mention(text: str, exclude_uid) -> Optional[dict]:
    """
    Scans a message for an @username or a known first name that belongs to
    someone OTHER than the sender, and returns that person's index doc
    ({"_id", "name", "username"}) if found. Used to "link" two people's chat
    memories when one asks about the other by name.
    """
    if mongo_users_col is None:
        return None
    try:
        for uname in re.findall(r"@(\w+)", text):
            doc = await mongo_users_col.find_one({"username": {"$regex": f"^{re.escape(uname)}$", "$options": "i"}})
            if doc and doc["_id"] != str(exclude_uid):
                return doc
        words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)}
        if words:
            async for doc in mongo_users_col.find({"_id": {"$ne": str(exclude_uid)}}):
                nm = (doc.get("name") or "").strip().lower()
                if nm and nm in words:
                    return doc
    except Exception as e:
        logger.error(f"[mongo find_user_by_mention] {e}")
    return None


async def build_linked_context(uid, user_name: str, username: str, text: str) -> str:
    """
    Updates the sender's own name-index entry, then checks if their message
    is asking about a DIFFERENT known user by name/@username. If so, pulls
    that person's recent chat memory and hands it to the AI as a labeled
    block, so Beluga can talk about them naturally — like a friend who
    actually remembers the group, not two isolated bots.
    """
    await update_user_index(uid, user_name, username)
    other = await find_user_by_mention(text, uid)
    if not other:
        return ""
    other_hist = build_chat_history_context(await get_user_memory(other["_id"]))
    if not other_hist:
        return ""
    label = other.get("name") or other.get("username") or "them"
    return f"\n\n{user_name} is asking about {label}. Here's what you remember chatting with {label} recently:{other_hist}"


async def delete_user_memory(user_id) -> bool:
    if not _mongo_available():
        return False
    try:
        await mongo_memory_col.delete_one({"_id": str(user_id)})
        return True
    except Exception as e:
        logger.error(f"[mongo delete_user_memory] {e}")
        return False


async def clear_all_memory() -> tuple:
    """Wipe every user's memory doc from MongoDB. Returns (deleted_count, failed)."""
    if not _mongo_available():
        return (0, 1)
    try:
        result = await mongo_memory_col.delete_many({})
        return (result.deleted_count, 0)
    except Exception as e:
        logger.error(f"[mongo clear_all_memory] {e}")
        return (0, 1)
