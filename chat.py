"""AI chat in DMs and groups (ghost mode, guest messages, sticker replies)."""
import re
import uuid
from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from ai_models import ai
from config import STICKER_PACK_ALT, STICKER_PACK_MAIN, logger
from database import (
    append_chat_history,
    build_chat_history_context,
    build_linked_context,
    build_memory_context,
    get_random_sticker_from,
    get_user_memory,
)
from handlers.search import _run_search_and_reply, _screenshot_website
from state import away_mode_state, bot_status
from texts import CHAT_PROMPT, DM_SECRETARY_PROMPT
from utils import get_user_name, is_owner, mood_hint


async def monitor_private_chat(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Plain AI chat for DMs by default. If /away mode is ON and the sender
    isn't the owner, replies come from DM_SECRETARY_PROMPT instead — short,
    deflects "where's [owner]" questions, stays in character, no chat
    history/memory used (stateless secretary behavior) and no sticker spam.
    """
    if not u.message or u.effective_chat.type != "private":
        return
    try:
        text = (u.message.text or u.message.caption or "").strip()
        if not text or text.startswith("/"):
            return

        uid = u.effective_user.id
        is_away_dm = away_mode_state["on"] and not is_owner(uid)

        if is_away_dm:
            user_name = get_user_name(u.effective_user)
            system = f"{DM_SECRETARY_PROMPT}\nThe person's name is {user_name}."
            reply = await ai(system, text, T.MONITOR_PRIVATE_CHAT_FALLBACK_1, max_tok=120)
            try:
                await u.message.reply_text(reply, reply_to_message_id=u.message.message_id)
            except Exception:
                pass
            return

        ss_match = re.match(r"^ss\s+(\S+)", text, re.IGNORECASE)
        if ss_match:
            target = ss_match.group(1)
            if not target.startswith("http"):
                target = "https://" + target
            await _screenshot_website(u, c, target)
            return

        search_match = re.match(r"^(?:search|google)\s+(.+)", text, re.IGNORECASE)
        if search_match:
            await _run_search_and_reply(u, c, search_match.group(1).strip())
            return

        user_name = get_user_name(u.effective_user)
        username = u.effective_user.username or ""
        memory = await get_user_memory(uid)
        mem_ctx = build_memory_context(memory)
        hist_ctx = build_chat_history_context(memory)
        linked_ctx = await build_linked_context(uid, user_name, username, text)
        system = f"{CHAT_PROMPT}\nThe user's name is {user_name}.{mem_ctx}{hist_ctx}{linked_ctx}{mood_hint(text)}"
        reply = await ai(system, text, T.monitor_private_chat_fallback_2(user_name), max_tok=250)

        try:
            await u.message.reply_text(reply, reply_to_message_id=u.message.message_id)
        except Exception:
            pass

        await append_chat_history(uid, text, reply, user_name, username)

        stick = await get_random_sticker_from(next_ai_sticker_pack())
        if stick:
            try:
                await c.bot.send_sticker(chat_id=u.effective_chat.id, sticker=stick)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[monitor_private_chat] {e}")


async def monitor_ghost_mode(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or u.effective_chat.type == "private":
        return
    text = (u.message.text or "").strip()
    if not text:
        return

    bot_username = bot_status.get("username", "")
    text_lower = text.lower()

    mentioned = False
    if bot_username and f"@{bot_username}" in text_lower:
        mentioned = True
    if "@smartbeluga_bot" in text_lower:
        mentioned = True
    if not mentioned:
        return

    msg_content = re.sub(r"@\w+", "", text, flags=re.IGNORECASE).strip()
    if not msg_content:
        return

    try:
        uid = u.effective_user.id
        user_name = get_user_name(u.effective_user)
        username = u.effective_user.username or ""
        memory = await get_user_memory(uid)
        mem_ctx = build_memory_context(memory)
        hist_ctx = build_chat_history_context(memory)
        linked_ctx = await build_linked_context(uid, user_name, username, msg_content)
        system = f"{CHAT_PROMPT}\nThe user's name is {user_name}.{mem_ctx}{hist_ctx}{linked_ctx}{mood_hint(text)}"
        reply = await ai(system, msg_content, T.monitor_ghost_mode_fallback_1(user_name), max_tok=250)
        await u.message.reply_text(reply, reply_to_message_id=u.message.message_id)
        await append_chat_history(uid, msg_content, reply, user_name, username)
    except Exception as e:
        logger.error(f"[monitor_ghost_mode] {e}")


ai_reply_counter = {}
_ai_sticker_toggle = {"n": 0}


def next_ai_sticker_pack() -> str:
    """Alternates between the main pack and the new pybelu pack on every AI
    reply that attaches a sticker — main pack first, then pybelu, then main..."""
    _ai_sticker_toggle["n"] += 1
    return STICKER_PACK_MAIN if _ai_sticker_toggle["n"] % 2 == 1 else STICKER_PACK_ALT


async def guest_message_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Handles Telegram's Guest Bots feature (Bot API 10.0): someone @-mentions
    the bot in a group chat it is NOT a member of. Telegram delivers this as
    Update.guest_message instead of the usual Update.message, and requires a
    reply via Bot.answer_guest_query() instead of send_message — the bot has
    no membership in that chat, so it can't post there directly.
    Limitations (per Telegram): no chat history access, exactly one reply
    per guest_message, fully stateless — so no memory/history lookup here.
    """
    gm = getattr(u, "guest_message", None)
    if not gm:
        return
    try:
        guest_query_id = getattr(gm, "guest_query_id", None)
        user_text = (gm.text or gm.caption or "").strip()
        if not guest_query_id:
            return
        if not user_text:
            reply_text = "Meow? Summon me with an actual question! 🐾"
        else:
            reply_text = await ai(CHAT_PROMPT, user_text, T.GUEST_MESSAGE_FALLBACK_1, max_tok=250)

        result = InlineQueryResultArticle(
            id=str(uuid.uuid4())[:32],
            title="Beluga 🐾",
            input_message_content=InputTextMessageContent(reply_text, parse_mode=ParseMode.MARKDOWN),
        )
        await c.bot.answer_guest_query(guest_query_id=guest_query_id, result=result)
    except Exception as e:
        logger.error(f"[guest_message] {e}")


async def sticker_reply_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """When someone sends the bot a sticker directly (DM) or replies to the
    bot's message with a sticker (group), reply back with a sticker too."""
    if not u.message or not u.message.sticker:
        return
    try:
        is_dm = u.effective_chat.type == "private"
        is_reply_to_bot = bool(
            u.message.reply_to_message
            and u.message.reply_to_message.from_user
            and u.message.reply_to_message.from_user.id == c.bot.id
        )
        if not (is_dm or is_reply_to_bot):
            return
        stick = await get_random_sticker_from(next_ai_sticker_pack())
        if stick:
            await c.bot.send_sticker(
                chat_id=u.effective_chat.id, sticker=stick,
                reply_to_message_id=u.message.message_id
            )
    except Exception as e:
        logger.error(f"[sticker_reply] {e}")
