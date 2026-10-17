"""Photo / video / sticker understanding."""
import asyncio
import os
import re
import time
import base64
import cv2
import io
import uuid
from PIL import Image
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes
from typing import Optional
import texts as T
from ai_models import _call_vision, ai
from config import MEDIA_MEMORY_SECONDS, logger
from state import bot_status, last_chat_media
from texts import CHAT_PROMPT
from utils import get_user_name


IMAGE_QUERY_RE = re.compile(
    r"what('?s| is)?\s*(in|this|it)|ye\s*kya|yeh\s*kya|kya\s*hai\s*(is|ye|yeh)|"
    r"what('?s| is)?\s*(happening|going on)|is\s*mein\s*kya",
    re.IGNORECASE,
)


def _extract_video_frame_b64(buf: io.BytesIO, suffix: str = ".mp4") -> Optional[str]:
    """Grabs ONE middle frame from a video via cv2 (already a dependency for
    QR scanning — no extra weight) and returns it as base64 JPEG. suffix lets
    callers pass ".webm" for Telegram's video stickers vs ".mp4" for regular
    videos — cv2/ffmpeg mostly sniff content over extension, but matching it
    avoids edge-case container misdetection."""
    tmp_path = f"/tmp/{uuid.uuid4().hex}{suffix}"
    try:
        with open(tmp_path, "wb") as f:
            f.write(buf.read())
        cap = cv2.VideoCapture(tmp_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return None
        ok2, jpg = cv2.imencode(".jpg", frame)
        if not ok2:
            return None
        return base64.b64encode(jpg.tobytes()).decode()
    except Exception as e:
        logger.error(f"[_extract_video_frame_b64] {e}")
        return None
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _webp_bytes_to_jpeg_b64(raw: bytes) -> Optional[str]:
    """Converts a static Telegram sticker (.webp) to base64 JPEG via PIL
    (already a dependency for /watermark etc.) — vision APIs are far more
    reliably tested against jpeg/png than webp."""
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        return base64.b64encode(out.getvalue()).decode()
    except Exception as e:
        logger.error(f"[_webp_bytes_to_jpeg_b64] {e}")
        return None


async def _fetch_media_b64(bot, photo=None, video=None, sticker=None) -> tuple:
    """
    Unified downloader for the three media kinds Beluga can be asked about.
    Returns (image_b64_or_None, error_reason_or_None) — error_reason is only
    set when b64 is None, so callers can show something more specific than
    a generic failure (e.g. Lottie stickers genuinely can't be read).
    """
    loop = asyncio.get_running_loop()
    try:
        buf = io.BytesIO()
        if photo:
            f = await bot.get_file(photo[-1].file_id)
            await f.download_to_memory(buf)
            buf.seek(0)
            return base64.b64encode(buf.read()).decode(), None
        if video:
            f = await bot.get_file(video.file_id)
            await f.download_to_memory(buf)
            buf.seek(0)
            b64 = await loop.run_in_executor(None, _extract_video_frame_b64, buf, ".mp4")
            return b64, (None if b64 else "couldn't read that video")
        if sticker:
            if getattr(sticker, "is_animated", False):
                # .tgs — vector Lottie animation, not a raster/video format;
                # decoding it needs a Lottie renderer we don't carry.
                return None, "that's an animated (Lottie) sticker — I can't peek inside those yet, only static or video ones"
            f = await bot.get_file(sticker.file_id)
            await f.download_to_memory(buf)
            buf.seek(0)
            if getattr(sticker, "is_video", False):
                b64 = await loop.run_in_executor(None, _extract_video_frame_b64, buf, ".webm")
                return b64, (None if b64 else "couldn't read that video sticker")
            b64 = await loop.run_in_executor(None, _webp_bytes_to_jpeg_b64, buf.read())
            return b64, (None if b64 else "couldn't read that sticker")
    except Exception as e:
        logger.error(f"[_fetch_media_b64] {e}")
    return None, "couldn't read that file"


MEDIA_HINT_RE = re.compile(
    r"\b(image|img|pic|picture|photo|sticker|video|gif|this|that|it|ye|yeh|isme|is\s*mein|kya|dekh|see|look|bta|bata|tell)\b",
    re.IGNORECASE,
)


def _record_media(u: Update):
    """Passively remember the latest media in this chat (whoever sent it)."""
    m = u.message
    if not m or not (m.photo or m.video or m.sticker) or not u.effective_chat:
        return
    last_chat_media[str(u.effective_chat.id)] = {
        "photo": m.photo, "video": m.video, "sticker": m.sticker,
        "uid": u.effective_user.id if u.effective_user else 0,
        "name": get_user_name(u.effective_user) if u.effective_user else "someone",
        "ts": time.time(),
    }


async def _wants_media_description(text: str) -> bool:
    """Uses AI to judge whether a message is asking what's in the recent
    image/sticker/video (works for Hinglish/typos too, unlike a fixed regex)."""
    if IMAGE_QUERY_RE.search(text):
        return True
    if len(text) > 160 or not MEDIA_HINT_RE.search(text):
        return False
    verdict = await ai(
        T.WANTS_MEDIA_DESCRIPTION_PROMPT_1,
        text, fallback=T.WANTS_MEDIA_DESCRIPTION_FALLBACK_1, max_tok=5)
    return verdict.strip().upper().startswith("YES")


async def _beluga_media_reply(description: str, question: str, user_name: str, kind: str) -> str:
    """Turns the raw vision description into Beluga's own voice, so it reads
    like her talking about something she just looked at — not a bare caption."""
    system = (
        f"{CHAT_PROMPT}\nThe user's name is {user_name}.\n"
        f"You just LOOKED at a {kind} in this chat yourself. What you saw: {description}\n"
        "Now answer their message directly about it, in your own voice (2-3 short sentences max). "
        "Say what's actually in it. NEVER ask them to resend it or say you can't see it."
    )
    return await ai(system, question, fallback=description, max_tok=220)


def _media_kind(photo, video, sticker) -> str:
    return "sticker" if sticker else ("video" if video else "photo")


async def _answer_about_media(u: Update, c: ContextTypes.DEFAULT_TYPE, photo, video, sticker, question: str):
    sm = await u.message.reply_text(T.ANSWER_ABOUT_MEDIA_MSG_1, parse_mode=ParseMode.MARKDOWN)
    try:
        image_b64, err = await _fetch_media_b64(c.bot, photo=photo, video=video, sticker=sticker)
        if not image_b64:
            msg = (err or "couldn't read that file")
            await sm.edit_text(T.answer_about_media_msg_2(msg))
            return
        kind = _media_kind(photo, video, sticker)
        desc = await _call_vision(
            [image_b64],
            f"Describe this {kind} in detail: subjects, characters, any text, emotions, colours, setting. "
            f"The user asked: \"{question}\"")
        if not desc:
            await sm.edit_text(T.ANSWER_ABOUT_MEDIA_MSG_3)
            return
        reply = await _beluga_media_reply(desc, question, get_user_name(u.effective_user), kind)
        try:
            await sm.edit_text(reply)
        except Exception:
            await u.message.reply_text(reply)
    except Exception as e:
        logger.error(f"[_answer_about_media] {e}")
        try:
            await sm.edit_text(T.ANSWER_ABOUT_MEDIA_MSG_4)
        except Exception:
            pass


async def image_understanding_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Fires on every photo/video/sticker: ALWAYS remembers it (so later "what's
    in it?" questions work), and answers right away if tagged/questioned in
    the caption or sent as a reply to Beluga. Stickers have no caption, so
    only reply-to-bot triggers an instant sticker answer.
    """
    if not u.message:
        return
    photo, video, sticker = u.message.photo, u.message.video, u.message.sticker
    if not photo and not video and not sticker:
        return
    _record_media(u)
    caption = (u.message.caption or "").strip()
    bot_username = bot_status.get("username", "")
    mentioned = bool(bot_username) and f"@{bot_username}" in caption.lower()
    is_question = bool(IMAGE_QUERY_RE.search(caption))
    is_reply_to_bot = bool(
        u.message.reply_to_message
        and u.message.reply_to_message.from_user
        and u.message.reply_to_message.from_user.id == c.bot.id
    )
    if not (mentioned or is_question or is_reply_to_bot):
        return
    await _answer_about_media(u, c, photo, video, sticker, caption or "what's in this?")


async def media_reply_question_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Text-side: someone asks about media. Uses (1) the media they replied to,
    else (2) the latest photo/video/sticker Beluga saw in this chat (last 15
    min). AI decides whether the message is really asking what's in it.
    Raises ApplicationHandlerStop only when it answers, so normal chat flows on.
    """
    if not u.message or not u.message.text or not u.effective_chat:
        return
    text = u.message.text.strip()
    rm = u.message.reply_to_message
    photo = video = sticker = None
    replied_media = bool(rm and (rm.photo or rm.video or rm.sticker))
    if replied_media:
        photo, video, sticker = rm.photo, rm.video, rm.sticker
    else:
        mem = last_chat_media.get(str(u.effective_chat.id))
        if not mem or time.time() - mem["ts"] > MEDIA_MEMORY_SECONDS:
            return
        # For un-replied questions in groups, only engage when addressed to Beluga
        # (tag / reply to her) or in DM — avoids hijacking normal human chatter.
        bot_username = bot_status.get("username", "")
        addressed = (
            u.effective_chat.type == "private"
            or (bool(bot_username) and f"@{bot_username}" in text.lower())
            or bool(rm and rm.from_user and rm.from_user.id == c.bot.id)
            or "beluga" in text.lower()
        )
        if not addressed:
            return
        photo, video, sticker = mem["photo"], mem["video"], mem["sticker"]

    bot_username = bot_status.get("username", "")
    mentioned = bool(bot_username) and f"@{bot_username}" in text.lower()
    if not (mentioned or replied_media or u.effective_chat.type == "private" or "beluga" in text.lower()
            or (rm and rm.from_user and rm.from_user.id == c.bot.id)):
        return
    if not await _wants_media_description(text):
        return
    await _answer_about_media(u, c, photo, video, sticker, text)
    raise ApplicationHandlerStop
