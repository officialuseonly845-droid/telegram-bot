"""/mute, /ban, /stats and /peace mode."""
from collections import deque
from datetime import datetime, timedelta
from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from textblob import TextBlob
import texts as T
from config import (
    PEACE_ALERT_COOLDOWN_SEC,
    PEACE_WINDOW_SEC,
    PROFANITY_WORDS,
    SINGLE_ABUSE_THRESHOLD,
    logger,
)
from state import last_peace_alert, peace_mode_state, recent_negative_events
from utils import get_daily_stats, get_sentiment, get_user_name, is_group_admin


async def mute_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Admin-only: /mute (as a reply to the target's message) — restricts them from sending anything in this chat."""
    if not u.message or u.effective_chat.type == "private":
        return
    if not await is_group_admin(c.bot, u.effective_chat.id, u.effective_user.id):
        await u.message.reply_text(T.MUTE_MSG_1)
        return
    if not u.message.reply_to_message:
        await u.message.reply_text(T.MUTE_MSG_2, parse_mode=ParseMode.MARKDOWN)
        return
    target = u.message.reply_to_message.from_user
    try:
        await c.bot.restrict_chat_member(
            u.effective_chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False, can_send_audios=False, can_send_documents=False,
                                         can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
                                         can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False),
        )
        get_daily_stats(str(u.effective_chat.id))["mutes"] += 1
        await u.message.reply_text(T.mute_msg_3(get_user_name(target)), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await u.message.reply_text(T.mute_msg_4(e), parse_mode=ParseMode.MARKDOWN)


async def ban_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Admin-only: /ban (as a reply to the target's message) — bans them from this chat."""
    if not u.message or u.effective_chat.type == "private":
        return
    if not await is_group_admin(c.bot, u.effective_chat.id, u.effective_user.id):
        await u.message.reply_text(T.BAN_MSG_1)
        return
    if not u.message.reply_to_message:
        await u.message.reply_text(T.BAN_MSG_2, parse_mode=ParseMode.MARKDOWN)
        return
    target = u.message.reply_to_message.from_user
    try:
        await c.bot.ban_chat_member(u.effective_chat.id, target.id)
        get_daily_stats(str(u.effective_chat.id))["bans"] += 1
        await u.message.reply_text(T.ban_msg_3(get_user_name(target)), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await u.message.reply_text(T.ban_msg_4(e), parse_mode=ParseMode.MARKDOWN)


async def stats_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/stats — shows total members, messages/day, mutes/bans today, and most active users."""
    if not u.message or u.effective_chat.type == "private":
        await u.message.reply_text(T.STATS_MSG_1, parse_mode=ParseMode.MARKDOWN)
        return
    cid = str(u.effective_chat.id)
    try:
        member_count = await c.bot.get_chat_member_count(u.effective_chat.id)
    except Exception:
        member_count = "N/A"

    d = get_daily_stats(cid)
    top_users = sorted(d["user_counts"].items(), key=lambda kv: kv[1]["count"], reverse=True)[:5]

    if top_users:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        top_lines = "\n".join(
            f"{medals[i]}  __{e['name']}__\n     ✎ *{e['count']:,}* messages"
            for i, (uid, e) in enumerate(top_users)
        )
    else:
        top_lines = "_No activity yet today._"

    members_line = f"`{member_count:,}`" if isinstance(member_count, int) else f"`{member_count}`"
    today_label = datetime.now().strftime("%d %b %Y")
    text = (
        f"┏━━━━━━━━━━━━━━━━━━┓\n"
        f"┃   📊 *GROUP STATISTICS*   ┃\n"
        f"┗━━━━━━━━━━━━━━━━━━┛\n"
        f"_{today_label}_\n\n"
        f"👥 *Total Members*\n     {members_line}\n\n"
        f"💬 *Messages Today*\n     `{d['messages']:,}`\n\n"
        f"🔇 *Mutes Today*  ·  🔨 *Bans Today*\n     `{d['mutes']:,}`  ·  `{d['bans']:,}`\n\n"
        f"─────────────────────\n"
        f"🏆 *MOST ACTIVE TODAY*\n\n"
        f"{top_lines}\n\n"
        f"─────────────────────\n"
        f"_POWERED BY BELUGA.PY_ 🎀"
    )
    await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def peace_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Group-admin-only: /peace — shows current Peace Mode status with an
    inline ON/OFF toggle. While ON, this group's messages get scanned for
    fights/abuse (sentiment + keywords); it does NOT run in groups where
    this hasn't been explicitly turned on."""
    if not u.message or u.effective_chat.type == "private":
        await u.message.reply_text(T.PEACE_MSG_1, parse_mode=ParseMode.MARKDOWN)
        return
    if not await is_group_admin(c.bot, u.effective_chat.id, u.effective_user.id):
        await u.message.reply_text(T.PEACE_MSG_2)
        return
    cid = str(u.effective_chat.id)
    st = peace_mode_state.setdefault(cid, {"on": False})
    status = "ON ✅" if st["on"] else "OFF ❌"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ON", callback_data=f"peace:on:{cid}", style="success"),
        InlineKeyboardButton("❌ OFF", callback_data=f"peace:off:{cid}", style="danger"),
    ]])
    await u.message.reply_text(
        T.peace_msg_3(status),
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
    )


async def peace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        _, action, cid = q.data.split(":", 2)
        if not await is_group_admin(context.bot, int(cid), q.from_user.id):
            await q.answer(T.PEACE_MSG_4, show_alert=True)
            return
        st = peace_mode_state.setdefault(cid, {"on": False})
        st["on"] = (action == "on")
        recent_negative_events.pop(cid, None)
        await q.answer(T.PEACE_MSG_5)
        status = "ON ✅" if st["on"] else "OFF ❌"
        await q.edit_message_text(
            T.peace_msg_6(status),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"[peace_callback] {e}")


def is_negative_or_abusive(text: str) -> bool:
    """Fast, always-cheap check: VADER strong-negative OR a profanity hit.
    Ambiguous short-negative messages get a lightweight TextBlob second
    opinion (pure-python, no model download) instead of a heavy model."""
    if not text:
        return False
    low = text.lower()
    if any(w in low for w in PROFANITY_WORDS):
        return True
    score = get_sentiment(text).get("compound", 0.0)
    if score <= -0.6:
        return True
    if -0.6 < score <= -0.3 and len(text.split()) <= 6:
        try:
            return TextBlob(text).sentiment.polarity <= -0.3
        except Exception:
            return False
    return False


async def check_peace_mode(c: ContextTypes.DEFAULT_TYPE, cid: str, uid: int, name: str, text: str, now: datetime):
    """Only runs at all when Peace Mode is ON for this specific chat."""
    if not peace_mode_state.get(cid, {}).get("on"):
        return
    if not is_negative_or_abusive(text):
        return

    last_alert = last_peace_alert.get(cid)
    if last_alert and (now - last_alert).total_seconds() < PEACE_ALERT_COOLDOWN_SEC:
        return

    events = recent_negative_events.setdefault(cid, deque(maxlen=20))
    events.append((str(uid), name, now))
    cutoff = now - timedelta(seconds=PEACE_WINDOW_SEC)
    while events and events[0][2] < cutoff:
        events.popleft()

    distinct_users = {}
    for u_id, u_name, ts in events:
        distinct_users[u_id] = u_name

    try:
        if len(distinct_users) >= 2:
            names = list(distinct_users.values())[-2:]
            text_alert = (
                f"🚨 {names[0]} and {names[1]} stop fighting in the group and maintain peace 🙏 "
                f"Or else strict actions will be taken 😡.\n\n"
                f"Dear admins, mute them ASAP..."
            )
            await c.bot.send_message(int(cid), text_alert)
            last_peace_alert[cid] = now
            recent_negative_events[cid] = deque(maxlen=20)
        else:
            same_user_count = sum(1 for u_id, _, _ in events if u_id == str(uid))
            if same_user_count >= SINGLE_ABUSE_THRESHOLD:
                text_alert = (
                    f"🚨 {name} stop the abusive language and maintain peace in the group 🙏 "
                    f"Or else strict actions will be taken 😡.\n\n"
                    f"Dear admins, mute them ASAP..."
                )
                await c.bot.send_message(int(cid), text_alert)
                last_peace_alert[cid] = now
                recent_negative_events[cid] = deque(maxlen=20)
    except Exception as e:
        logger.error(f"[check_peace_mode] {e}")
