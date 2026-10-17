"""/shop, /mytag and custom tags."""
import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import CUSTOM_TAG_COST, CUSTOM_TAG_TIMEOUT_SEC, TAG_SHOP, _EMOJI_RE, logger
from database import sync_leaderboard_chat_to_mongo, sync_tags_to_mongo
from state import db, member_tags, pending_custom_tags
from styles import SHOP_IMAGE_URL
from utils import bump_score, get_user_name, send_photo_safe


async def shop_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/shop — spend game points on a cosmetic name tag (CEO/MANAGER/ACTIVE/HELPER), shown in /lb and /stats."""
    if not u.message or u.effective_chat.type == "private":
        await u.message.reply_text(T.SHOP_MSG_1, parse_mode=ParseMode.MARKDOWN)
        return
    cid = str(u.effective_chat.id)
    uid = str(u.effective_user.id)
    current_pts = db.get("scores", {}).get(cid, {}).get(uid, {}).get("score", 0)

    lines = [f"*{t['label']}* {t['emoji']}  —  *{t['cost']:,}* points" for t in TAG_SHOP]
    lines.append(f"*CUSTOM TAG* ✨  —  *{CUSTOM_TAG_COST:,}* points _(pick your own name!)_")
    text = (
        "🎀 *FOLLOWING TAGS ARE AVAILABLE FOR PURCHASE* 🎀\n"
        "━━━━━━━━━━━━━━━━━━━━\n" + "\n\n".join(lines) +
        f"\n━━━━━━━━━━━━━━━━━━━━\nYour points: *{current_pts:,}*"
    )
    row1 = [InlineKeyboardButton(str(t["cost"]), callback_data=f"shop:buy:{t['key']}", style=t["color"]) for t in TAG_SHOP[:2]]
    row2 = [InlineKeyboardButton(str(t["cost"]), callback_data=f"shop:buy:{t['key']}", style=t["color"]) for t in TAG_SHOP[2:]]
    row3 = [InlineKeyboardButton(f"✨ CUSTOM — {CUSTOM_TAG_COST:,}", callback_data="shop:buy:CUSTOM", style="success")]
    kb = InlineKeyboardMarkup([row1, row2, row3])

    sent = await send_photo_safe(c.bot, u.effective_chat.id, SHOP_IMAGE_URL, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    if not sent:
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def mytag_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/mytag — shows your OFFICIAL Telegram member tag (live from Telegram, not a local copy)."""
    if not u.message or u.effective_chat.type == "private":
        await u.message.reply_text(T.MYTAG_MSG_1, parse_mode=ParseMode.MARKDOWN)
        return
    try:
        member = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
        tag = getattr(member, "tag", None)
    except Exception as e:
        logger.error(f"[mytag] {e}")
        tag = None
    if tag:
        await u.message.reply_text(T.mytag_msg_2(tag), parse_mode=ParseMode.MARKDOWN)
    else:
        await u.message.reply_text(T.MYTAG_MSG_3, parse_mode=ParseMode.MARKDOWN)


async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.message:
        return
    try:
        _, _, tag_key = q.data.split(":", 2)

        if tag_key == "CUSTOM":
            cid, uid = str(q.message.chat_id), str(q.from_user.id)
            current_pts = db.get("scores", {}).get(cid, {}).get(uid, {}).get("score", 0)
            if current_pts < CUSTOM_TAG_COST:
                await q.answer(T.shop_buy_msg_1(current_pts), show_alert=True)
                return
            bump_score(cid, uid, get_user_name(q.from_user), -CUSTOM_TAG_COST)
            await sync_leaderboard_chat_to_mongo(cid)
            await q.answer(T.SHOP_BUY_MSG_2)
            user_name = get_user_name(q.from_user)
            prompt = await context.bot.send_message(
                q.message.chat_id,
                f"Hi {user_name} 🎀 CONGRATS ON BUYING THE EXCLUSIVE CUSTOM TAG! "
                f"SEND ME THE TAG NAME YOU WANT TO SET (reply to this message).",
            )
            pending_custom_tags[prompt.message_id] = {
                "cid": cid, "uid": uid, "user_name": user_name, "fulfilled": False,
            }
            asyncio.create_task(_expire_custom_tag_request(context, prompt.message_id))
            return

        tag = next((t for t in TAG_SHOP if t["key"] == tag_key), None)
        if not tag:
            await q.answer(T.SHOP_BUY_MSG_3, show_alert=True)
            return

        cid, uid = str(q.message.chat_id), str(q.from_user.id)
        current_pts = db.get("scores", {}).get(cid, {}).get(uid, {}).get("score", 0)
        if current_pts < tag["cost"]:
            await q.answer(T.shop_buy_msg_4(current_pts, tag), show_alert=True)
            return

        # Set the REAL official Telegram member tag (shows next to their name
        # in the actual group UI) — requires the bot to be an admin with the
        # "Edit Member Tags" (can_manage_tags) right turned on.
        try:
            await context.bot.set_chat_member_tag(
                chat_id=q.message.chat_id, user_id=q.from_user.id, tag=tag["label"],
            )
        except Exception as e:
            logger.error(f"[shop_buy] set_chat_member_tag failed: {e}")
            await q.answer(
                T.SHOP_BUY_MSG_5,
                show_alert=True,
            )
            return

        # Cache locally too, purely so our own /lb and /stats displays don't
        # need an extra API call per row — the line above is the source of truth.
        member_tags.setdefault(cid, {})[uid] = tag["label"]
        await sync_tags_to_mongo(cid)

        bump_score(cid, uid, get_user_name(q.from_user), -tag["cost"])
        await sync_leaderboard_chat_to_mongo(cid)
        await q.answer(T.shop_buy_msg_6(tag))
        try:
            await context.bot.send_message(
                q.message.chat_id,
                f"🎀 *{get_user_name(q.from_user)}* just bought the *{tag['label']}* {tag['emoji']} tag!",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[shop_buy_callback] {e}")


async def _expire_custom_tag_request(context: ContextTypes.DEFAULT_TYPE, prompt_msg_id: int):
    """If the user hasn't sent a valid tag name within 30 minutes, refund their points."""
    await asyncio.sleep(CUSTOM_TAG_TIMEOUT_SEC)
    req = pending_custom_tags.get(prompt_msg_id)
    if not req or req["fulfilled"]:
        return
    req["fulfilled"] = True  # stop the reply handler from also processing it
    bump_score(req["cid"], req["uid"], req["user_name"], CUSTOM_TAG_COST)
    await sync_leaderboard_chat_to_mongo(req["cid"])
    try:
        await context.bot.send_message(
            int(req["cid"]),
            f"⏰ {req['user_name']}, you didn't send a tag name in time — your "
            f"{CUSTOM_TAG_COST:,} points have been refunded.",
        )
    except Exception as e:
        logger.error(f"[_expire_custom_tag_request] {e}")
    pending_custom_tags.pop(prompt_msg_id, None)


async def handle_custom_tag_reply(u: Update, c: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if this message is a reply to a pending custom-tag prompt from
    the SAME user, and if so, validates + applies (or explains what's
    wrong and asks them to resend). Returns True if it handled the message
    (so the caller — monitor_group — should stop further processing).
    """
    if not u.message or not u.message.reply_to_message:
        return False
    prompt_id = u.message.reply_to_message.message_id
    req = pending_custom_tags.get(prompt_id)
    if not req or req["fulfilled"]:
        return False
    if str(u.effective_user.id) != req["uid"]:
        return False

    tag_name = (u.message.text or "").strip()
    problems = []
    if len(tag_name) > 16:
        problems.append(f"it's {len(tag_name)} characters — max is *16*")
    if _EMOJI_RE.search(tag_name):
        problems.append("it contains emojis, which aren't supported")
    if not tag_name:
        problems.append("it's empty")

    if problems:
        await u.message.reply_text(
            T.handle_custom_tag_reply_msg_1(problems),
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    try:
        await c.bot.set_chat_member_tag(chat_id=int(req["cid"]), user_id=int(req["uid"]), tag=tag_name)
    except Exception as e:
        logger.error(f"[handle_custom_tag_reply] set_chat_member_tag failed: {e}")
        await u.message.reply_text(
            T.HANDLE_CUSTOM_TAG_REPLY_MSG_2,
        )
        return True

    req["fulfilled"] = True
    member_tags.setdefault(req["cid"], {})[req["uid"]] = tag_name
    await sync_tags_to_mongo(req["cid"])
    pending_custom_tags.pop(prompt_id, None)
    await u.message.reply_text(T.handle_custom_tag_reply_msg_3(tag_name), parse_mode=ParseMode.MARKDOWN)
    return True
