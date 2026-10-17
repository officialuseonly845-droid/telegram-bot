"""Owner/admin commands: /away, /ping, /model, /clearmemory, /refreshstickers."""
import asyncio
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from ai_models import (
    CF_MODEL,
    GROQ_MODEL,
    NVIDIA_MODEL,
    OR_MODEL,
    _call_cloudflare,
    _call_groq,
    _call_nvidia,
    _call_openrouter,
    _cf_rate_limited,
    _groq_rate_limited,
    _nvi_rate_limited,
    _or_rate_limited,
    ai_model_state,
)
from config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_API_TOKEN,
    GROQ_KEY,
    NVIDIA_KEY,
    OPENROUTER_KEY,
    STICKER_PACK_ALT,
    STICKER_PACK_MAIN,
    STICKER_PACK_SAFE,
    STICKER_PACK_WELCOME,
    logger,
)
from database import clear_all_memory, load_sticker_pack
from state import away_mode_state, mongo_client
from utils import is_owner


async def refreshstickers_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Owner-only: re-pull every sticker pack from Telegram and re-save to
    MongoDB, so newly added stickers show up without waiting for a redeploy
    (startup already does this automatically on every GitHub deploy)."""
    if not u.message or not is_owner(u.effective_user.id if u.effective_user else 0):
        return
    for pack in (STICKER_PACK_MAIN, STICKER_PACK_SAFE, STICKER_PACK_ALT, STICKER_PACK_WELCOME):
        await load_sticker_pack(c.bot, pack)
    await u.message.reply_text(T.REFRESHSTICKERS_MSG_1)


async def away_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Owner-only: /away — toggle DM automation on/off.
    When ON, anyone who DMs the bot (except the owner) gets handled by the
    DM_SECRETARY_PROMPT persona instead of the normal chat persona — short,
    crunchy replies, deflects "where is [owner]" questions, never breaks
    character as an AI. Meant for when the owner is away and wants their
    DMs auto-managed without people realizing it's automated.
    """
    if not u.message:
        return
    if not is_owner(u.effective_user.id if u.effective_user else 0):
        await u.message.reply_text(T.AWAY_MSG_1)
        return
    away_mode_state["on"] = not away_mode_state["on"]
    if away_mode_state["on"]:
        await u.message.reply_text(
            T.AWAY_MSG_2,
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await u.message.reply_text(T.AWAY_MSG_3, parse_mode=ParseMode.MARKDOWN)


async def ping_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /ping — live health check. Actually calls Groq, OpenRouter, and MongoDB
    (not just checking if keys/URLs are set) and reports real latency + status
    for each, so you can tell at a glance if the bot is truly connected.
    """
    if not u.message:
        return
    sm = await u.message.reply_text(T.PING_MSG_1, parse_mode=ParseMode.MARKDOWN)

    # --- Groq live test ---
    groq_status, groq_ms = "❌ not reachable", None
    if GROQ_KEY:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(_call_groq("Reply with only the word: pong", "ping", 20), timeout=10)
            groq_ms = int((time.time() - t0) * 1000)
            groq_status = f"✅ OK ({groq_ms}ms)" if res else "⚠️ no response"
        except Exception as e:
            groq_status = f"❌ error: {str(e)[:40]}"
    else:
        groq_status = "❌ GROQ_API_KEY not set"

    # --- OpenRouter live test ---
    or_status = "❌ not reachable"
    if OPENROUTER_KEY:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(_call_openrouter("Reply with only the word: pong", "ping", 20), timeout=12)
            or_ms = int((time.time() - t0) * 1000)
            or_status = f"✅ OK ({or_ms}ms)" if res else "⚠️ no response"
        except Exception as e:
            or_status = f"❌ error: {str(e)[:40]}"
    else:
        or_status = "❌ OPENROUTER_API_KEY not set"

    # --- NVIDIA live test ---
    nvi_status = "❌ not reachable"
    if NVIDIA_KEY:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(_call_nvidia("Reply with only the word: pong", "ping", 20), timeout=34)
            nvi_ms = int((time.time() - t0) * 1000)
            nvi_status = f"✅ OK ({nvi_ms}ms)" if res else "⚠️ no response"
        except Exception as e:
            nvi_status = f"❌ error: {str(e)[:40]}"
    else:
        nvi_status = "❌ NVIDIA_API_KEY not set"

    # --- Cloudflare live test ---
    cf_status = "❌ not reachable"
    if CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(_call_cloudflare("Reply with only the word: pong", "ping", 20), timeout=14)
            cf_ms = int((time.time() - t0) * 1000)
            cf_status = f"✅ OK ({cf_ms}ms)" if res else "⚠️ no response"
        except Exception as e:
            cf_status = f"❌ error: {str(e)[:40]}"
    else:
        cf_status = "❌ CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set"

    # --- MongoDB live test ---
    mongo_status = "❌ not configured"
    if mongo_client is not None:
        t0 = time.time()
        try:
            await asyncio.wait_for(mongo_client.admin.command("ping"), timeout=8)
            mongo_ms = int((time.time() - t0) * 1000)
            mongo_status = f"✅ OK ({mongo_ms}ms)"
        except Exception as e:
            mongo_status = f"❌ error: {str(e)[:40]}"

    mode = ai_model_state["mode"]
    text = (
        f"🏓 *Live Connection Check*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *Groq* (`{GROQ_MODEL}`)\n{groq_status}\n\n"
        f"🌐 *OpenRouter* (`{OR_MODEL}`)\n{or_status}\n\n"
        f"⚡ *NVIDIA* (`{NVIDIA_MODEL}`)\n{nvi_status}\n\n"
        f"☁️ *Cloudflare* (`{CF_MODEL}`)\n{cf_status}\n\n"
        f"🗄 *MongoDB*\n{mongo_status}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Active AI mode: `{mode.upper()}`"
    )
    try:
        await sm.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def model_command_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Owner-only: /model — pick Groq, OpenRouter, NVIDIA, Cloudflare, or Auto via inline keyboard."""
    if not u.message:
        return
    if not is_owner(u.effective_user.id if u.effective_user else 0):
        await u.message.reply_text(T.MODEL_COMMAND_MSG_1)
        return

    mode = ai_model_state["mode"]
    groq_ok = "✅" if not _groq_rate_limited() else "⛔RL"
    or_ok = "✅" if not _or_rate_limited() else "⛔RL"
    nvi_ok = "✅" if not _nvi_rate_limited() else "⛔RL"
    cf_ok = "✅" if not _cf_rate_limited() else "⛔RL"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{'▶' if mode=='gro' else ''} GRO {groq_ok}", callback_data="model:gro", style="primary"),
        InlineKeyboardButton(f"{'▶' if mode=='rou' else ''} ROU {or_ok}", callback_data="model:rou", style="primary"),
        InlineKeyboardButton(f"{'▶' if mode=='nvi' else ''} NVI {nvi_ok}", callback_data="model:nvi", style="primary"),
        InlineKeyboardButton(f"{'▶' if mode=='cf' else ''} CF {cf_ok}", callback_data="model:cf", style="primary"),
    ], [
        InlineKeyboardButton(f"{'▶' if mode=='auto' else ''} AUTO", callback_data="model:auto", style="success"),
    ]])

    status = (
        f"🤖 *AI Model Selector*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Current: `{mode.upper()}`\n\n"
        f"• *GRO* — Groq `{GROQ_MODEL}` {groq_ok}\n"
        f"• *ROU* — OpenRouter `{OR_MODEL}` {or_ok}\n"
        f"• *NVI* — NVIDIA `{NVIDIA_MODEL}` {nvi_ok}\n"
        f"• *CF* — Cloudflare `{CF_MODEL}` {cf_ok}\n"
        f"• *AUTO* (default) — Groq → OpenRouter → NVIDIA → Cloudflare, falls back on rate limit or failure\n\n"
        f"_Rate limit auto-recovers after 20s_"
    )
    await u.message.reply_text(status, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
        if not is_owner(q.from_user.id):
            await q.answer(T.MODEL_MSG_1, show_alert=True)
            return
        _, mode = q.data.split(":", 1)
        ai_model_state["mode"] = mode
        label = {"gro": "Groq (GRO)", "rou": "OpenRouter (ROU)", "nvi": "NVIDIA (NVI)", "cf": "Cloudflare (CF)", "auto": "Auto Switch"}.get(mode, mode)
        await q.edit_message_text(
            T.model_msg_2(label, GROQ_MODEL, OR_MODEL, NVIDIA_MODEL, CF_MODEL),
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"[AI] Model mode switched to: {mode}")
    except Exception as e:
        logger.error(f"[model_callback] {e}")


async def clearmemory_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    if not is_owner(u.effective_user.id if u.effective_user else 0):
        await u.message.reply_text(T.CLEARMEMORY_MSG_1)
        return
    status_msg = await u.message.reply_text(T.CLEARMEMORY_MSG_2)
    deleted, failed = await clear_all_memory()
    if failed == 0:
        result_text = f"✅ Memory cleared! Removed {deleted} user record(s) from MongoDB."
    else:
        result_text = "⚠️ MongoDB not available or clear failed — check logs."
    try:
        await status_msg.edit_text(T.clearmemory_msg_3(result_text))
    except Exception:
        await u.message.reply_text(result_text)
