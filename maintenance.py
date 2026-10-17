"""/shutdown and /restart (owner only) plus the global maintenance gate.

While shutdown is active the gate swallows EVERY update (commands, chat,
callbacks, polls, member changes), so the bot does nothing anywhere. The only
reply it ever sends is the maintenance notice, and only when someone calls it
by name (or DMs it / replies to it). The flag lives in MongoDB, so it survives
restarts and redeploys until the owner sends /restart.
"""
import time
import uuid
from datetime import datetime
from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes
import texts as T
from config import logger
from database import save_maintenance_state
from state import bot_status, maintenance_state
from utils import is_owner

_NOTICE_COOLDOWN_SEC = 20
_last_notice: dict = {}   # chat_id -> monotonic time of the last maintenance notice


def _command_of(text: str):
    """'/Restart@MyBot now' -> 'restart'. None if the text isn't a command."""
    if not text or not text.startswith("/"):
        return None
    return text.split()[0][1:].split("@")[0].lower()


def _is_call_to_bot(u: Update, c: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """True when the message is talking TO Beluga: name, @username, reply to her, or any DM."""
    msg = u.effective_message
    chat = u.effective_chat
    if chat is not None and chat.type == "private":
        return True
    low = text.lower()
    if "beluga" in low:
        return True
    username = bot_status.get("username", "")
    if username and (username in low or f"@{username}" in low):
        return True
    rep = msg.reply_to_message if msg else None
    return bool(rep and rep.from_user and rep.from_user.id == c.bot.id)


async def _send_notice(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg = u.effective_message
    chat = u.effective_chat
    if not msg or not chat:
        return
    now = time.monotonic()
    if now - _last_notice.get(chat.id, 0.0) < _NOTICE_COOLDOWN_SEC:
        return
    _last_notice[chat.id] = now
    try:
        await msg.reply_text(T.MAINTENANCE_NOTICE, reply_to_message_id=msg.message_id)
    except Exception as e:
        logger.warning(f"[maintenance] notice failed: {e}")


async def maintenance_gate(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Registered at the earliest handler group. No-op unless shutdown is active."""
    if not maintenance_state["on"]:
        return

    # Someone @-mentioned Beluga from a chat she isn't in (Guest Bots). That is
    # a call by name, so she answers with the notice, exactly once per query.
    gm = getattr(u, "guest_message", None)
    if gm is not None:
        qid = getattr(gm, "guest_query_id", None)
        if qid:
            try:
                result = InlineQueryResultArticle(
                    id=str(uuid.uuid4())[:32], title="Beluga 🐾",
                    input_message_content=InputTextMessageContent(T.MAINTENANCE_NOTICE),
                )
                await c.bot.answer_guest_query(guest_query_id=qid, result=result)
            except Exception as e:
                logger.warning(f"[maintenance] guest notice failed: {e}")
        raise ApplicationHandlerStop

    msg = u.effective_message
    user = u.effective_user
    text = ((msg.text or msg.caption or "") if msg else "").strip()
    cmd = _command_of(text)

    # The owner's switch commands are the only thing allowed through.
    if cmd in ("restart", "shutdown") and user and is_owner(user.id):
        return

    # Commands stay completely silent. Plain messages get the notice only if
    # they are addressed to Beluga. Edited messages never trigger a reply.
    if msg and text and cmd is None and not u.edited_message and _is_call_to_bot(u, c, text):
        await _send_notice(u, c)
    raise ApplicationHandlerStop


async def shutdown_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/shutdown (owner only): silence the bot everywhere until /restart."""
    if not u.message or not u.effective_user or not is_owner(u.effective_user.id):
        return
    if maintenance_state["on"]:
        await u.message.reply_text(T.SHUTDOWN_MSG_ALREADY, parse_mode=ParseMode.MARKDOWN)
        return
    maintenance_state["on"] = True
    maintenance_state["since"] = datetime.now().isoformat()
    saved = await save_maintenance_state(True)
    text = T.SHUTDOWN_MSG_1 + ("" if saved else T.SHUTDOWN_MSG_NO_DB)
    try:
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"[shutdown] confirmation failed: {e}")
    logger.warning("[maintenance] SHUTDOWN activated by owner")


async def restart_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/restart (owner only): wake the bot up again."""
    if not u.message or not u.effective_user or not is_owner(u.effective_user.id):
        return
    if not maintenance_state["on"]:
        await u.message.reply_text(T.RESTART_MSG_ALREADY)
        return
    maintenance_state["on"] = False
    maintenance_state["since"] = None
    await save_maintenance_state(False)
    _last_notice.clear()
    await u.message.reply_text(T.RESTART_MSG_1, parse_mode=ParseMode.MARKDOWN)
    logger.warning("[maintenance] Bot restarted by owner")
