"""Group message monitor: anti-spam, anti-raid lockdown, stats, AI replies."""
import asyncio
import re
import time
from collections import deque
from datetime import datetime, timedelta
from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from ai_models import ai, ai_emoji
from config import (
    FLOOD_COUNT,
    FLOOD_WINDOW_SEC,
    LOCK_COUNTDOWN_SEC,
    LOCK_DURATION_SEC,
    RAID_USER_THRESHOLD,
    RAID_WINDOW_SEC,
    STICKER_PACK_SAFE,
    logger,
)
from database import (
    append_chat_history,
    build_chat_history_context,
    build_linked_context,
    build_memory_context,
    get_random_sticker_from,
    get_user_memory,
    is_pack_banned,
    register_chat,
)
from handlers.chat import ai_reply_counter, next_ai_sticker_pack
from handlers.moderation import check_peace_mode
from handlers.search import _run_search_and_reply, _screenshot_website
from handlers.shop import handle_custom_tag_reply
from state import (
    bot_status,
    db,
    known_chats,
    last_group_activity,
    lockdown_state,
    peace_mode_state,
    pending_custom_tags,
    raid_flags,
    recent_group_msgs,
    spam_tracker,
)
from texts import CHAT_PROMPT
from utils import get_daily_stats, get_user_name, is_group_admin, mood_hint, safe_react


async def trigger_lockdown(c: ContextTypes.DEFAULT_TYPE, cid: int):
    """
    Anti-raid group lockdown. Uses a monotonic-clock deadline (not a naive
    sleep(1) loop) so the countdown never drifts even if a Telegram edit
    call is slow or rate-limited — it always recomputes 'seconds remaining'
    from the real clock, and only edits the message when the displayed
    number actually changes (avoids spammy edits / 'message not modified').
    """
    cid_s = str(cid)
    if lockdown_state.get(cid_s, {}).get("locking") or lockdown_state.get(cid_s, {}).get("active"):
        return
    lockdown_state[cid_s] = {"locking": True, "active": False, "prevent": False}

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("PREVENT LOCK 🔐", callback_data=f"antiraid:prevent:{cid_s}", style="danger")]])
    try:
        msg = await c.bot.send_message(
            cid,
            "🚨 *SPAMMING DETECTED 😡*\n*LOCKING GROUP IN 10 SECONDS.*\n\n_POWERED BY BELUGA.PY 🎀_",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"[antiraid] couldn't send lock warning in {cid}: {e}")
        lockdown_state.pop(cid_s, None)
        return

    deadline = time.monotonic() + LOCK_COUNTDOWN_SEC
    last_shown = None
    while True:
        if lockdown_state.get(cid_s, {}).get("prevent"):
            try:
                await c.bot.edit_message_text(
                    chat_id=cid, message_id=msg.message_id,
                    text=T.TRIGGER_LOCKDOWN_MSG_1,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            lockdown_state.pop(cid_s, None)
            return
        remaining = max(0, round(deadline - time.monotonic()))
        if remaining != last_shown:
            try:
                await c.bot.edit_message_text(
                    chat_id=cid, message_id=msg.message_id,
                    text=T.trigger_lockdown_msg_2(remaining),
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
                )
            except Exception:
                pass
            last_shown = remaining
        if remaining <= 0:
            break
        await asyncio.sleep(0.4)

    try:
        await c.bot.set_chat_permissions(cid, ChatPermissions(
            can_send_messages=False, can_send_audios=False, can_send_documents=False,
            can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
            can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
            can_add_web_page_previews=False,
        ))
    except Exception as e:
        logger.error(f"[antiraid] failed to lock {cid}: {e}")

    lockdown_state[cid_s] = {"locking": False, "active": True, "prevent": False}
    try:
        await c.bot.edit_message_text(
            chat_id=cid, message_id=msg.message_id,
            text=T.TRIGGER_LOCKDOWN_MSG_3,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    await asyncio.sleep(max(0, LOCK_DURATION_SEC - LOCK_COUNTDOWN_SEC))

    deadline2 = time.monotonic() + LOCK_COUNTDOWN_SEC
    last_shown = None
    while True:
        remaining = max(0, round(deadline2 - time.monotonic()))
        if remaining != last_shown:
            try:
                await c.bot.edit_message_text(
                    chat_id=cid, message_id=msg.message_id,
                    text=T.trigger_lockdown_msg_4(remaining),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            last_shown = remaining
        if remaining <= 0:
            break
        await asyncio.sleep(0.4)

    try:
        await c.bot.set_chat_permissions(cid, ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True,
            can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
            can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
            can_add_web_page_previews=True,
        ))
    except Exception as e:
        logger.error(f"[antiraid] failed to unlock {cid}: {e}")

    try:
        await c.bot.edit_message_text(
            chat_id=cid, message_id=msg.message_id,
            text=T.TRIGGER_LOCKDOWN_MSG_5,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass
    lockdown_state.pop(cid_s, None)


async def antiraid_prevent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        _, _, cid_s = q.data.split(":", 2)
        if not await is_group_admin(context.bot, int(cid_s), q.from_user.id):
            await q.answer(T.ANTIRAID_PREVENT_MSG_1, show_alert=True)
            return
        st = lockdown_state.get(cid_s)
        if not st or not st.get("locking"):
            await q.answer(T.ANTIRAID_PREVENT_MSG_2, show_alert=True)
            return
        st["prevent"] = True
        await q.answer(T.ANTIRAID_PREVENT_MSG_3)
    except Exception as e:
        logger.error(f"[antiraid_prevent_callback] {e}")


async def monitor_group(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.effective_user or u.effective_user.is_bot:
        return
    if u.effective_chat.type == "private":
        return
    try:
        uid, cid, now = u.effective_user.id, str(u.effective_chat.id), datetime.now()
        text_early = (u.message.text or u.message.caption or "").strip()

        if pending_custom_tags and u.message.reply_to_message and u.message.text:
            handled = await handle_custom_tag_reply(u, c)
            if handled:
                return

        if u.message.sticker:
            pack_of_sticker = getattr(u.message.sticker, "set_name", None)
            if is_pack_banned(pack_of_sticker):
                try:
                    await u.message.delete()
                except Exception:
                    pass
                return

        spam_tracker.setdefault(uid, [])
        spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < timedelta(seconds=FLOOD_WINDOW_SEC)]
        spam_tracker[uid].append(now)
        if len(spam_tracker[uid]) >= FLOOD_COUNT:
            try: await u.message.delete()
            except Exception: pass

            if not lockdown_state.get(cid, {}).get("locking") and not lockdown_state.get(cid, {}).get("active"):
                flags = raid_flags.setdefault(cid, {})
                flags[str(uid)] = time.time()
                cutoff = time.time() - RAID_WINDOW_SEC
                for k in list(flags.keys()):
                    if flags[k] < cutoff:
                        del flags[k]
                if len(flags) >= RAID_USER_THRESHOLD:
                    raid_flags[cid] = {}
                    asyncio.create_task(trigger_lockdown(c, u.effective_chat.id))
            return

        dstat = get_daily_stats(cid)
        dstat["messages"] += 1
        uentry = dstat["user_counts"].setdefault(str(uid), {"name": get_user_name(u.effective_user), "count": 0})
        uentry["name"] = get_user_name(u.effective_user)
        uentry["count"] += 1
        if dstat["messages"] % 500 == 0 and dstat["messages"] not in dstat["milestones_hit"]:
            dstat["milestones_hit"].add(dstat["messages"])
            try:
                await c.bot.send_message(int(cid), f"*{dstat['messages']} messages reached 💖*", parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

        if text_early and peace_mode_state.get(cid, {}).get("on"):
            asyncio.create_task(check_peace_mode(c, cid, uid, get_user_name(u.effective_user), text_early, now))

        db.setdefault("seen", {}).setdefault(cid, {})[str(uid)] = {
            "id": uid, "un": u.effective_user.username, "n": u.effective_user.first_name or "User"
        }
        counts = db.setdefault("counts", {})
        counts[cid] = counts.get(cid, 0) + 1

        if counts[cid] % 64 == 0:
            stick_safe = await get_random_sticker_from(STICKER_PACK_SAFE)
            if stick_safe:
                try:
                    await c.bot.send_sticker(chat_id=u.effective_chat.id, sticker=stick_safe)
                except Exception:
                    pass

        text = (u.message.text or u.message.caption or "").strip()
        if not text:
            bot_status["message_count"] += 1
            return

        # Conversation-revival bookkeeping — cheap, pure logic, no AI call here.
        last_group_activity[cid] = now
        if not text.startswith("/"):
            recent_group_msgs.setdefault(cid, deque(maxlen=15)).append(text[:200])

        # Fallback registration for the chat registry — covers chats Beluga was
        # already in before this feature existed (no my_chat_member event
        # would have fired for those historically).
        if cid not in known_chats:
            asyncio.create_task(register_chat(u.effective_chat))

        ss_match = re.match(r"^ss\s+(\S+)", text, re.IGNORECASE)
        if ss_match:
            target = ss_match.group(1)
            if not target.startswith("http"):
                target = "https://" + target
            await _screenshot_website(u, c, target)
            bot_status["message_count"] += 1
            return

        search_match = re.match(r"^(?:search|google)\s+(.+)", text, re.IGNORECASE)
        if search_match:
            await _run_search_and_reply(u, c, search_match.group(1).strip())
            bot_status["message_count"] += 1
            return

        if text.startswith("/"):
            bot_status["message_count"] += 1
            return

        bot_username = bot_status.get("username", "")
        text_low = text.lower()
        contains_beluga = "beluga" in text_low
        contains_username = bool(bot_username) and (bot_username in text_low or f"@{bot_username}" in text_low)
        is_reply = (u.message.reply_to_message and u.message.reply_to_message.from_user
                    and u.message.reply_to_message.from_user.id == c.bot.id)

        if contains_beluga or contains_username or is_reply:
            try: await asyncio.wait_for(c.bot.send_chat_action(u.effective_chat.id, "typing"), timeout=4.0)
            except Exception: pass

            emoji = await ai_emoji(text)
            try: await safe_react(c.bot, u.effective_chat.id, u.message.message_id, emoji)
            except Exception: pass

            user_name = get_user_name(u.effective_user)
            username = u.effective_user.username or ""
            memory = await get_user_memory(uid)
            mem_ctx = build_memory_context(memory)
            hist_ctx = build_chat_history_context(memory)
            linked_ctx = await build_linked_context(uid, user_name, username, text)
            system = f"{CHAT_PROMPT}\nThe user's name is {user_name}.{mem_ctx}{hist_ctx}{linked_ctx}{mood_hint(text)}"
            reply = await ai(system, text, T.monitor_group_fallback_1(user_name), max_tok=250)

            try:
                await u.message.reply_text(reply, reply_to_message_id=u.message.message_id)
            except Exception:
                pass

            await append_chat_history(uid, text, reply, user_name, username)

            ai_reply_counter[cid] = ai_reply_counter.get(cid, 0) + 1
            if ai_reply_counter[cid] % 2 == 0:
                stick = await get_random_sticker_from(next_ai_sticker_pack())
                if stick:
                    try:
                        await c.bot.send_sticker(chat_id=u.effective_chat.id, sticker=stick)
                    except Exception:
                        pass

        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[monitor_group] {e}")
