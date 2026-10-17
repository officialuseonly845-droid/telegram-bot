"""/lb, /nw and /gm attendance."""
import asyncio
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import logger
from database import sync_leaderboard_chat_to_mongo, sync_weekly_to_mongo
from state import bot_status, db, gm_msg_lock, gm_tracker
from styles import GM_IMAGE_URL, LB_IMAGE_URL, MEDALS, _build_gm_caption
from utils import bump_score, is_owner, tagged_name


async def lb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid = str(u.effective_chat.id)
        local_scores = db.get("scores", {}).get(cid, {})
        lb = sorted(local_scores.values(), key=lambda x: x.get("score", 0), reverse=True)
        seen_ids = set()
        clean_lb = [e for e in lb if e.get("user_id") not in seen_ids and not seen_ids.add(e.get("user_id"))]

        lw = db.get("weekly", {}).get(cid, {})
        lines = []
        if lw and lw.get("top3"):
            lines.append("🏆 *LAST WEEK CHAMPIONS* 🏆\n")
            lines.extend([f"{MEDALS[i]} {e.get('name','?')[:18]} — {e.get('score',0):,} pts" for i, e in enumerate(lw["top3"])])
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n")

        lines += ["╔════════════════════════════╗", "🏆  *CURRENT LEADERBOARD*  🏆", "╚════════════════════════════╝\n"]
        if not clean_lb:
            lines.append("No scores yet! Play some games 🎮")
        else:
            for i, e in enumerate(clean_lb[:10]):
                m = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
                disp_name = tagged_name(cid, str(e.get("user_id", "")), e.get("name", "Unknown"))[:18]
                lines.append(f"{m} `{disp_name:<18}` `{e.get('score',0):>6,} pts`")
        lines += ["\n━━━━━━━━━━━━━━━━━━━━", "➕ +10 quiz/ttt  ·  +700 mine  ·  +50 gm"]
        text = "\n".join(lines)

        try:
            await u.message.reply_photo(photo=LB_IMAGE_URL, caption=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[lb] {e}")


async def nw_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    New Week reset:
    1. Read CURRENT in-memory scores for this chat.
    2. Compute top 3 -> store as this chat's "weekly" champions (kept, not reset).
    3. Wipe this chat's scores.
    4. Push both changes to MongoDB immediately.
    """
    if not u.message:
        return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text(T.NW_MSG_1)
            return
        cid = str(u.effective_chat.id)
        lb = sorted(db.get("scores", {}).get(cid, {}).values(), key=lambda x: x.get("score", 0), reverse=True)
        seen_ids = set()
        clean_lb = [e for e in lb if e.get("user_id") not in seen_ids and not seen_ids.add(e.get("user_id"))]
        top3 = [{"name": e.get("name", "?"), "score": e.get("score", 0)} for e in clean_lb[:3]]
        wk_label = datetime.now().strftime("%d %b %Y")
        db.setdefault("weekly", {})[cid] = {"top3": top3, "week_label": wk_label}
        db["scores"][cid] = {}
        await sync_weekly_to_mongo(cid)
        await sync_leaderboard_chat_to_mongo(cid)

        announce = ["🏆🎉 *NEW WEEK!* 🎉🏆", f"\n_Week: {wk_label}_\n", "👑 *Champions:*\n"]
        announce.extend([f"{MEDALS[i]} *{e['name']}* — {e['score']:,} pts" for i, e in enumerate(top3)])
        announce.extend(["\n🔄 *All scores reset!*", "🚀 _New battle begins!_"])
        await u.message.reply_text("\n".join(announce), parse_mode=ParseMode.MARKDOWN)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[nw] {e}")


async def gm_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text(T.GM_MSG_1)
            return
        cid = str(u.effective_chat.id)
        date_str = datetime.now().strftime("%d %b %Y")
        msg = None
        try:
            msg = await u.message.reply_photo(
                photo=GM_IMAGE_URL, caption=_build_gm_caption([], date_str),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}", style="success")]])
            )
        except Exception:
            msg = await u.message.reply_text(
                text=_build_gm_caption([], date_str),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}", style="success")]])
            )
        if msg:
            gm_tracker[cid] = (msg.message_id, [], date_str)
            gm_msg_lock[cid] = asyncio.Lock()
    except Exception as e:
        logger.error(f"[gm] {e}")


async def gm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        parts = q.data.split(":")
        cid = parts[2]
        gm_msg_lock.setdefault(cid, asyncio.Lock())
        async with gm_msg_lock[cid]:
            if cid not in gm_tracker:
                await q.answer(T.GM_MSG_2)
                return
            msg_id, users, date_str = gm_tracker[cid]
            user, user_id = q.from_user, str(q.from_user.id)
            if any(uu.get("id") == user_id for uu in users):
                await q.answer(T.GM_MSG_3)
                return
            u_name = (user.first_name or "User")[:20]
            utime = datetime.now().strftime("%H:%M")
            users.append({"id": user_id, "name": u_name, "time": utime})
            gm_tracker[cid] = (msg_id, users, date_str)
            bump_score(str(q.message.chat_id), user_id, u_name, +50)
            await sync_leaderboard_chat_to_mongo(str(q.message.chat_id))
            try:
                new_cap = _build_gm_caption(users, date_str)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("GM 🥱", callback_data=f"gm:attend:{cid}", style="success")]])
                if q.message.photo:
                    await context.bot.edit_message_caption(chat_id=q.message.chat_id, message_id=msg_id, caption=new_cap, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                else:
                    await context.bot.edit_message_text(chat_id=q.message.chat_id, message_id=msg_id, text=new_cap, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                await q.answer(T.gm_msg_4(u_name))
            except Exception:
                await q.answer(T.GM_MSG_5)
    except Exception as e:
        logger.error(f"[gm_callback] {e}")
