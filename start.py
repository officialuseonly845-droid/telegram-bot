"""/start, /workflow and the inline menu."""
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from config import STICKER_PACK_WELCOME, logger
from database import get_random_sticker_from
from styles import (
    START_MENU_IMAGE_PAGE,
    WORKFLOW_IMAGE_PAGE,
    _back_only_kb,
    _start_main_menu_kb,
)
from texts import START_TEXT, WHAT_I_CAN_DO_TEXT, WORKFLOW_TEXT
from utils import send_photo_safe


async def start_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    stick = await get_random_sticker_from(STICKER_PACK_WELCOME)
    if stick:
        try:
            await c.bot.send_sticker(chat_id=u.effective_chat.id, sticker=stick)
        except Exception:
            pass
    kb = _start_main_menu_kb()
    sent = await send_photo_safe(
        c.bot, u.effective_chat.id, START_MENU_IMAGE_PAGE,
        caption=START_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=kb
    )
    if not sent:
        # send_photo_safe already sent the text fallback if photo failed;
        # this only fires if BOTH photo and text somehow failed, so retry once more.
        try:
            await u.message.reply_text(START_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except Exception as e:
            logger.error(f"[start_handler] Total failure: {e}")


async def workflow_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    sent = await send_photo_safe(
        c.bot, u.effective_chat.id, WORKFLOW_IMAGE_PAGE,
        caption=WORKFLOW_TEXT, parse_mode=ParseMode.MARKDOWN
    )
    if not sent:
        try:
            await u.message.reply_text(WORKFLOW_TEXT, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"[workflow_handler] Final fallback failed: {e}")


async def start_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles all 3 submenu buttons + the BACK button on /start's inline menu.
    Edits the existing message in place (text or caption, whichever the
    message actually has) instead of sending new messages every tap.
    """
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
        action = q.data.split(":", 1)[1]

        if action == "whatido":
            target_text, target_kb = WHAT_I_CAN_DO_TEXT, _back_only_kb()
        elif action == "workflow":
            target_text, target_kb = WORKFLOW_TEXT, _back_only_kb()
        elif action == "back":
            target_text, target_kb = START_TEXT, _start_main_menu_kb()
        else:
            return

        try:
            if q.message.photo:
                await q.edit_message_caption(caption=target_text, parse_mode=ParseMode.MARKDOWN, reply_markup=target_kb)
            else:
                await q.edit_message_text(text=target_text, parse_mode=ParseMode.MARKDOWN, reply_markup=target_kb)
        except Exception as e:
            logger.warning(f"[start_menu_callback] Edit failed ({e}), sending fresh message instead.")
            await context.bot.send_message(chat_id=q.message.chat_id, text=target_text,
                                            parse_mode=ParseMode.MARKDOWN, reply_markup=target_kb)
    except Exception as e:
        logger.error(f"[start_menu_callback] {e}")
