"""/quiz and poll answers."""
import json
import random
import re
import time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from typing import Optional
import texts as T
from ai_models import ai
from config import logger
from database import sync_leaderboard_chat_to_mongo
from state import active_polls, bot_status, quiz_cooldown
from texts import FALLBACK_QS, QUIZ_TOPICS
from utils import bump_score, q_hash, safe_react


def quiz_on_cooldown(cid: str, question: str) -> bool:
    return time.time() < quiz_cooldown.get(cid, {}).get(q_hash(question), 0)


def mark_quiz(cid: str, question: str):
    quiz_cooldown.setdefault(cid, {})[q_hash(question)] = time.time() + 3600


async def gen_quiz(topic: str, cid: str) -> Optional[dict]:
    for attempt in range(3):
        try:
            raw = await ai(T.GEN_QUIZ_PROMPT_1,
                           T.gen_quiz_usermsg_1(topic),
                           "", max_tok=200)
            if not raw:
                logger.warning(f"[gen_quiz] attempt {attempt+1}: empty AI response")
                continue
            m = re.search(r"\{[\s\S]+\}", raw)
            if not m:
                logger.warning(f"[gen_quiz] attempt {attempt+1}: no JSON in AI response")
                continue
            d = json.loads(m.group(0))
            q = str(d.get("question", "")).strip()
            opts = d.get("options", [])
            idx = int(d.get("correct_index", 0))
            fact = str(d.get("fun_fact", "Meow!")).strip()
            if not q or len(opts) != 4 or not (0 <= idx <= 3):
                logger.warning(f"[gen_quiz] attempt {attempt+1}: malformed quiz data")
                continue
            if quiz_on_cooldown(cid, q):
                continue
            return {"question": q, "options": opts, "correct_index": idx, "fun_fact": fact}
        except Exception as e:
            logger.warning(f"[gen_quiz] attempt {attempt+1} error: {e}")
    return None


async def quiz_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        parts = u.message.text.split(maxsplit=1)
        topic = (parts[1].strip() if len(parts) > 1 else None) or random.choice(QUIZ_TOPICS)
        cid, cid_i = str(u.effective_chat.id), u.effective_chat.id
        await safe_react(c.bot, cid_i, u.message.message_id, "💡")
        sm = await u.message.reply_text(T.QUIZ_MSG_1, parse_mode=ParseMode.MARKDOWN)
        qdata = await gen_quiz(topic, cid)
        try:
            await sm.delete()
        except Exception:
            pass
        if qdata:
            mark_quiz(cid, qdata["question"])
            try:
                pm = await c.bot.send_poll(
                    chat_id=cid_i, question=f"🐱 {qdata['question'][:255]}",
                    options=[str(o)[:100] for o in qdata["options"]],
                    type="quiz", correct_option_id=qdata["correct_index"],
                    is_anonymous=False, explanation=qdata["fun_fact"][:200]
                )
                active_polls[pm.poll.id] = {"chat_id": cid_i, "correct_index": qdata["correct_index"]}
                bot_status["message_count"] += 1
                return
            except Exception as e:
                logger.warning(f"[quiz] AI-generated poll send failed, trying fallback bank: {e}")
        fb = random.choice(FALLBACK_QS)
        mark_quiz(cid, fb["q"])
        try:
            pm = await c.bot.send_poll(chat_id=cid_i, question=f"🐱 {fb['q']}", options=fb["opts"], type="quiz",
                                        correct_option_id=fb["ans"], is_anonymous=False, explanation=fb["fact"])
            active_polls[pm.poll.id] = {"chat_id": cid_i, "correct_index": fb["ans"]}
            bot_status["message_count"] += 1
        except Exception as e:
            logger.error(f"[quiz] Fallback poll send also failed: {e}")
            await u.message.reply_text(
                T.QUIZ_MSG_2,
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception as e:
        logger.error(f"[quiz] {e}")


async def poll_answer_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        ans = u.poll_answer
        if not ans:
            return
        info = active_polls.get(ans.poll_id)
        if not info or not ans.option_ids or ans.option_ids[0] != info["correct_index"]:
            return
        cid, uid = str(info["chat_id"]), str(ans.user.id)
        name = (ans.user.first_name or "?")[:30]
        bump_score(cid, uid, name, +10)
        await sync_leaderboard_chat_to_mongo(cid)
    except Exception:
        pass
