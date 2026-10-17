"""/pump, /dump, /gay, /couple."""
import random
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import OWNER_ID, logger
from database import sync_leaderboard_chat_to_mongo
from state import db, fun_cache_lock, fun_db
from utils import bump_score, is_owner


async def pump_dump_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        if not is_owner(u.effective_user.id if u.effective_user else 0):
            await u.message.reply_text(T.PUMP_DUMP_MSG_1)
            return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text(T.PUMP_DUMP_MSG_2)
            return
        parts = u.message.text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await u.message.reply_text(T.PUMP_DUMP_MSG_3)
            return
        amount = int(parts[1])
        cmd = parts[0].lstrip("/").lower().split("@")[0]
        delta = +amount if cmd == "pump" else -amount
        target, cid = u.message.reply_to_message.from_user, str(u.effective_chat.id)
        new_sc = bump_score(cid, str(target.id), (target.first_name or "User")[:30], delta)
        await sync_leaderboard_chat_to_mongo(cid)
        emoji = "🚀" if cmd == "pump" else "📉"
        sign = "+" if delta > 0 else ""
        await u.message.reply_text(
            T.pump_dump_msg_4(emoji, cmd, target, delta, sign, amount, new_sc),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"[pump_dump] {e}")


async def fun_dispatcher(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid = str(u.effective_chat.id)
        db.setdefault("seen", {}).setdefault(cid, {})
        cmd = u.message.text.lower().split()[0].lstrip("/").split("@")[0]
        pool = dict(db.get("seen", {}).get(cid, {}))
        # Telegram's Bot API has no "list all members" call, even for admin bots.
        # The one real member list a bot can read is the admin list, so merge
        # it in on top of everyone Beluga has seen chatting.
        try:
            for adm in await c.bot.get_chat_administrators(u.effective_chat.id):
                au = adm.user
                if au.is_bot:
                    continue
                pool.setdefault(str(au.id), {"id": au.id, "un": au.username or "", "n": au.first_name or au.username or "User"})
        except Exception as e:
            logger.warning(f"[fun_dispatcher] admin list failed: {e}")
        active_users = list(pool.values())
        if len(active_users) < (2 if cmd == "couple" else 1) and OWNER_ID:
            active_users.append({"id": OWNER_ID, "un": "Owner", "n": "Owner"})
        if len(active_users) < (2 if cmd == "couple" else 1):
            await u.message.reply_text(T.FUN_DISPATCHER_MSG_1)
            return
        day = datetime.now().strftime("%y-%m-%d")
        lk = f"{cid}:{cmd}:{day}"
        async with fun_cache_lock:
            cached = fun_db.get("gay_couple_log", {}).get(lk)
            if cached and cached.get("date") == day:
                await u.message.reply_text(cached["result"], parse_mode=ParseMode.MARKDOWN)
                return
        if cmd == "couple":
            m = random.sample(active_users, min(2, len(active_users)))
            res = f"💖 *{m[0]['n']}* 💞 *{m[1]['n']}*\n100% compatible!" if len(m) == 2 else f"💖 *{m[0]['n']}* needs a partner! 💔"
        else:
            m = [random.choice(active_users)]
            res = f"🌈 *{m[0]['n']}* is today's rainbow! 🌈"
        fun_db.setdefault("gay_couple_log", {})[lk] = {"date": day, "result": res, "users": [p.get("id") for p in m]}
        await u.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[fun_dispatcher] {e}")
