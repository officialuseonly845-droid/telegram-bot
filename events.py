"""Group events: bot added/removed, welcome messages, chat-revival loop."""
import asyncio
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from ai_models import ai
from config import (
    INACTIVITY_THRESHOLD_SEC,
    OWNER_ID,
    REVIVAL_AUTOEXPIRE_SEC,
    REVIVAL_COOLDOWN_SEC,
    WELCOME_AUTOEXPIRE_SEC,
    logger,
)
from database import register_chat, unregister_chat
from state import (
    last_group_activity,
    last_revival_time,
    last_welcome_msg,
    maintenance_state,
    recent_group_msgs,
)
from utils import _delete_after, get_user_name


async def my_chat_member_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Keeps the chat registry in sync whenever Beluga is added to
    or removed from a group/channel, and DMs the owner on every fresh add
    (whether added as a plain member or straight to admin) with the group
    name and an invite link where one can be obtained."""
    cmu = u.my_chat_member
    if not cmu:
        return
    try:
        old_status = cmu.old_chat_member.status
        new_status = cmu.new_chat_member.status
        was_in_chat = old_status in ("member", "administrator", "creator")
        is_in_chat = new_status in ("member", "administrator", "creator")

        if is_in_chat:
            await register_chat(cmu.chat)
            if not was_in_chat:
                asyncio.create_task(_notify_owner_added(c, cmu.chat, new_status, cmu.from_user, is_new_add=True))
            elif old_status == "member" and new_status == "administrator":
                asyncio.create_task(_notify_owner_added(c, cmu.chat, new_status, cmu.from_user, is_new_add=False))
        elif not is_in_chat and was_in_chat:
            await unregister_chat(cmu.chat.id)
    except Exception as e:
        logger.error(f"[my_chat_member_handler] {e}")


async def _notify_owner_added(c: ContextTypes.DEFAULT_TYPE, chat, status: str, added_by, is_new_add: bool = True):
    """DMs the owner: which group Beluga just got added to (or promoted in),
    by whom, whether she's admin, and an invite link if one can be gotten."""
    if not OWNER_ID:
        return
    link = None
    try:
        if chat.username:
            link = f"https://t.me/{chat.username}"
        elif status == "administrator":
            link = await c.bot.export_chat_invite_link(chat.id)
    except Exception as e:
        logger.warning(f"[_notify_owner_added] couldn't get invite link: {e}")

    adder = get_user_name(added_by) if added_by else "Unknown"
    if is_new_add:
        header = "🐋 *BELUGA WAS ADDED TO A NEW CHAT*"
        role_line = "👑 Made *admin* immediately" if status == "administrator" else "👤 Added as a regular member"
        who_line = f"👋 *Added by:* {adder}\n"
    else:
        header = "🐋 *BELUGA WAS PROMOTED TO ADMIN*"
        role_line = "👑 Now has *admin* rights"
        who_line = f"👋 *Promoted by:* {adder}\n"

    text = f"{header}\n\n📌 *Group:* {chat.title or 'Untitled'}\n{role_line}\n{who_line}"
    if link:
        text += f"🔗 *Link:* {link}\n"
    else:
        text += "🔗 *Link:* _private group, no link available_\n"
    try:
        await c.bot.send_message(OWNER_ID, text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[_notify_owner_added] failed to DM owner: {e}")


async def welcome_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Smart welcome: only the LATEST welcome message ever exists per chat —
    a new join deletes the previous welcome (even if its own 3-min timer
    hasn't fired yet) before sending the new one, preventing join-spam."""
    if not u.message or not u.message.new_chat_members:
        return
    cid = str(u.effective_chat.id)
    for member in u.message.new_chat_members:
        if member.id == c.bot.id:
            continue
        prev_id = last_welcome_msg.get(cid)
        if prev_id:
            try:
                await c.bot.delete_message(int(cid), prev_id)
            except Exception:
                pass

        mention = f"@{member.username}" if member.username else get_user_name(member)
        text = (
            f"🐋 *WELCOME*\n\n"
            f"👋 Welcome {mention}!\n\n"
            f"🎉 Glad to have you here.\n\n"
            f"📜 Check the rules\n"
            f"💬 Join the conversation"
        )
        try:
            msg = await c.bot.send_message(int(cid), text, parse_mode=ParseMode.MARKDOWN)
            last_welcome_msg[cid] = msg.message_id
            asyncio.create_task(_delete_after(c.bot, int(cid), msg.message_id, WELCOME_AUTOEXPIRE_SEC))
        except Exception as e:
            logger.error(f"[welcome_handler] {e}")


async def revival_loop(app):
    """
    Conversation revival: checks every 5 minutes for groups that have gone
    quiet past INACTIVITY_THRESHOLD_SEC. When one is found (and not on
    cooldown), it uses the recent chat context to ask the AI for ONE short,
    relevant conversation-starter — this is the one place in this feature
    set where an AI call is genuinely necessary, since a natural, on-topic
    opener can't be produced by pure regex/logic. Sends it, auto-deletes
    after 3 minutes, and resets both activity + cooldown so it won't
    immediately re-trigger or repeat the same topic.
    """
    while True:
        await asyncio.sleep(300)
        if maintenance_state["on"]:
            continue
        try:
            now = datetime.now()
            for cid, last_act in list(last_group_activity.items()):
                if (now - last_act).total_seconds() < INACTIVITY_THRESHOLD_SEC:
                    continue
                last_rev = last_revival_time.get(cid)
                if last_rev and (now - last_rev).total_seconds() < REVIVAL_COOLDOWN_SEC:
                    continue
                msgs = list(recent_group_msgs.get(cid, []))
                if not msgs:
                    continue
                context_text = "\n".join(msgs[-8:])
                try:
                    opener = await ai(
                        T.REVIVAL_PROMPT_1,
                        T.revival_usermsg_1(context_text),
                        "", max_tok=120,
                    )
                except Exception as e:
                    logger.warning(f"[revival] ai() failed: {e}")
                    opener = None
                if not opener:
                    continue
                text = f"🐋 *BELUGA*\n\n{opener}"
                try:
                    msg = await app.bot.send_message(int(cid), text, parse_mode=ParseMode.MARKDOWN)
                    asyncio.create_task(_delete_after(app.bot, int(cid), msg.message_id, REVIVAL_AUTOEXPIRE_SEC))
                    last_revival_time[cid] = now
                    last_group_activity[cid] = now
                except Exception as e:
                    logger.error(f"[revival] send failed: {e}")
        except Exception as e:
            logger.error(f"[revival_loop] {e}")
