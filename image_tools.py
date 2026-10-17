"""/qr, /scanqr and /watermark."""
import asyncio
import cv2
import io
import numpy as np
import qrcode
from PIL import Image, ImageDraw
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import logger
from state import wm_sessions
from styles import (
    VIBGYOR_COLORS,
    WM_STYLES,
    _build_color_keyboard,
    _build_style_keyboard,
    load_font,
)


async def qr_generate_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await u.message.reply_text(T.QR_GENERATE_MSG_1)
            return
        payload = parts[1].strip()
        sm = await u.message.reply_text(T.QR_GENERATE_MSG_2, parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        def _build():
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(payload)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white")
        img = await loop.run_in_executor(None, _build)
        bio = io.BytesIO()
        img.save(bio, "PNG")
        bio.seek(0)
        await sm.delete()
        await u.message.reply_photo(photo=bio, caption=T.QR_GENERATE_MSG_3)
    except Exception as e:
        logger.error(f"[qr_generate] {e}")


async def qr_scan_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text(T.QR_SCAN_MSG_1)
        return
    try:
        sm = await u.message.reply_text(T.QR_SCAN_MSG_2, parse_mode=ParseMode.MARKDOWN)
        photo = u.message.reply_to_message.photo[-1]
        file_obj = await c.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file_obj.download_to_memory(buf)
        buf.seek(0)
        loop = asyncio.get_running_loop()
        def _decode():
            arr = np.frombuffer(buf.getvalue(), dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            detector = cv2.QRCodeDetector()
            val, _, _ = detector.detectAndDecode(img)
            return val
        decoded_text = await loop.run_in_executor(None, _decode)
        if decoded_text:
            await sm.edit_text(T.qr_scan_msg_3(decoded_text), parse_mode=ParseMode.MARKDOWN)
        else:
            await sm.edit_text(T.QR_SCAN_MSG_4)
    except Exception as e:
        logger.error(f"[qr_scan] {e}")


def _wrap_text(draw, text: str, font, max_width: int) -> list:
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip() if current else word
        try:
            bbox = draw.textbbox((0, 0), test, font=font)
            test_w = bbox[2] - bbox[0]
        except Exception:
            test_w = len(test) * (font.size // 2)
        if test_w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
                current = word
            else:
                lines.append(word)
                current = ""
    if current:
        lines.append(current)
    return lines if lines else [text]


def _apply_watermark(buf: io.BytesIO, wm_text: str, font_size: int, color_key: str, style_name: str) -> io.BytesIO:
    im = Image.open(buf).convert("RGBA")
    img_w, img_h = im.size
    rgba = VIBGYOR_COLORS.get(color_key, (255, 255, 255, 220))
    style_key = WM_STYLES.get(style_name, "normal")

    margin_x = max(10, int(img_w * 0.04))
    max_text_width = img_w - 2 * margin_x

    chosen_font, chosen_lines = None, None
    for try_size in range(font_size, 11, -2):
        font = load_font(style_key, try_size)
        tmp_draw = ImageDraw.Draw(Image.new("RGBA", (img_w, img_h)))
        lines = _wrap_text(tmp_draw, wm_text, font, max_text_width)
        try:
            lb = tmp_draw.textbbox((0, 0), "Ay", font=font)
            line_h = (lb[3] - lb[1]) + int(try_size * 0.25)
        except Exception:
            line_h = try_size + 4
        total_h = line_h * len(lines)
        if total_h <= img_h * 0.4:
            chosen_font, chosen_lines = font, lines
            break

    if not chosen_font:
        chosen_font = load_font(style_key, 12)
        tmp_draw = ImageDraw.Draw(Image.new("RGBA", (img_w, img_h)))
        chosen_lines = _wrap_text(tmp_draw, wm_text, chosen_font, max_text_width)

    txt_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(txt_layer)
    try:
        lb = draw.textbbox((0, 0), "Ay", font=chosen_font)
        line_h = (lb[3] - lb[1]) + int(chosen_font.size * 0.25)
    except Exception:
        line_h = chosen_font.size + 4

    total_text_h = line_h * len(chosen_lines)
    block_top = img_h - total_text_h - 20
    shadow_color = (0, 0, 0, 140)
    shadow_offset = max(1, chosen_font.size // 20)

    for i, line in enumerate(chosen_lines):
        try:
            lb2 = draw.textbbox((0, 0), line, font=chosen_font)
            lw = lb2[2] - lb2[0]
        except Exception:
            lw = len(line) * (chosen_font.size // 2)
        x = (img_w - lw) // 2
        y = block_top + i * line_h
        draw.text((x + shadow_offset, y + shadow_offset), line, font=chosen_font, fill=shadow_color)
        draw.text((x, y), line, font=chosen_font, fill=rgba)

    combined = Image.alpha_composite(im, txt_layer)
    out_b = io.BytesIO()
    combined.convert("RGB").save(out_b, "JPEG", quality=92)
    out_b.seek(0)
    return out_b


async def watermark_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    if not u.message.reply_to_message or not u.message.reply_to_message.photo:
        await u.message.reply_text(T.WATERMARK_MSG_1)
        return
    parts = u.message.text.split(maxsplit=1)
    wm_text = parts[1].strip() if len(parts) > 1 else "BELUGAPY"
    uid = u.effective_user.id
    cid = u.effective_chat.id
    photo = u.message.reply_to_message.photo[-1]
    wm_sessions[uid] = {"text": wm_text, "file_id": photo.file_id, "chat_id": cid, "step": "font_size"}
    sizes = [16, 24, 32, 40, 52, 64, 80, 96]
    rows, row = [], []
    for sz in sizes:
        row.append(InlineKeyboardButton(str(sz), callback_data=f"wm:size:{uid}:{sz}", style="primary"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    await u.message.reply_text(T.watermark_msg_2(wm_text), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))


async def watermark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
        parts = q.data.split(":", 3)
        _, step, owner_uid_str, value = parts
        owner_uid = int(owner_uid_str)
        if q.from_user.id != owner_uid:
            await q.answer(T.WATERMARK_MSG_3, show_alert=True)
            return
        sess = wm_sessions.get(owner_uid)
        if not sess:
            await q.edit_message_text(T.WATERMARK_MSG_4)
            return

        if step == "size":
            sess["font_size"] = int(value)
            sess["step"] = "color"
            await q.edit_message_text(T.watermark_msg_5(sess, value), parse_mode=ParseMode.MARKDOWN, reply_markup=_build_color_keyboard(owner_uid))

        elif step == "color":
            sess["color"] = value
            sess["step"] = "style"
            await q.edit_message_text(T.watermark_msg_6(sess, value), parse_mode=ParseMode.MARKDOWN, reply_markup=_build_style_keyboard(owner_uid))

        elif step == "style":
            sess["style"] = value
            await q.edit_message_text(T.WATERMARK_MSG_7, parse_mode=ParseMode.MARKDOWN)
            try:
                file_obj = await context.bot.get_file(sess["file_id"])
                buf = io.BytesIO()
                await file_obj.download_to_memory(buf)
                buf.seek(0)
                font_size = sess.get("font_size", 40)
                color_key = sess.get("color", "⚪ White")
                style_name = sess.get("style", "Normal")
                wm_text = sess["text"]
                loop = asyncio.get_running_loop()
                res_b = await loop.run_in_executor(None, _apply_watermark, buf, wm_text, font_size, color_key, style_name)
                await context.bot.send_photo(
                    chat_id=sess["chat_id"], photo=res_b,
                    caption=T.watermark_msg_8(wm_text, font_size, color_key, style_name),
                    parse_mode=ParseMode.MARKDOWN
                )
                wm_sessions.pop(owner_uid, None)
            except Exception as e:
                logger.error(f"[wm_apply] {e}")
                await q.edit_message_text(T.watermark_msg_9(e), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[wm_callback] {e}")
