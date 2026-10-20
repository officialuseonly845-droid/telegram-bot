"""Entry point: health server, error handler, handler registration and startup. Run with `python bot.py`."""

# ── handlers location shim ───────────────────────────────────────────────────
# The handler modules are imported as `handlers.<name>`. They work whether they
# sit in a real `handlers/` folder OR are all uploaded flat next to this file
# (chat.py, crypto.py, search.py, ...). No renaming needed either way.
import os as _os
import sys as _sys
import types as _types

_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
if not _os.path.isdir(_os.path.join(_HERE, "handlers")):
    _pkg = _types.ModuleType("handlers")
    _pkg.__path__ = [_HERE]
    _sys.modules["handlers"] = _pkg
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import sys
from aiohttp import web
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    Application as TGApp,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    TypeHandler,
    filters,
)
import texts as T
from config import (
    BOT_TOKEN,
    HTTP_PORT,
    OWNER_ID,
    STICKER_PACK_ALT,
    STICKER_PACK_MAIN,
    STICKER_PACK_SAFE,
    STICKER_PACK_WELCOME,
    logger,
)
from database import (
    load_persistent_data,
    load_sticker_pack,
    mongo_cleanup_loop,
    periodic_sync,
    save_all_data,
)
from handlers.chat import (
    guest_message_handler,
    monitor_ghost_mode,
    monitor_private_chat,
    sticker_reply_handler,
)
from handlers.crypto import (
    crypto_chart_handler,
    crypto_movers_handler,
    crypto_price_handler,
    init_exchange_async,
)
from handlers.events import my_chat_member_handler, revival_loop, welcome_handler
from handlers.fun import fun_dispatcher, pump_dump_handler
from handlers.games import (
    cleanup_expired_games,
    mine_callback,
    mine_handler,
    tictac_handler,
    ttt_callback,
)
from handlers.group_monitor import antiraid_prevent_callback, monitor_group
from handlers.image_tools import (
    qr_generate_handler,
    qr_scan_handler,
    watermark_callback,
    watermark_handler,
)
from handlers.leaderboard import gm_callback, gm_handler, lb_handler, nw_handler
from handlers.maintenance import maintenance_gate, restart_handler, shutdown_handler
from handlers.media import image_understanding_handler, media_reply_question_handler
from handlers.moderation import (
    ban_handler,
    mute_handler,
    peace_callback,
    peace_handler,
    stats_handler,
)
from handlers.news import execute_news_flow
from handlers.owner import (
    clearmemory_handler,
    model_callback,
    model_command_handler,
    ping_handler,
    refreshstickers_handler,
)
from handlers.quiz import poll_answer_handler, quiz_handler
from handlers.search import bananalogic_handler, search_handler, ss_handler
from handlers.shop import mytag_handler, shop_buy_callback, shop_handler
from handlers.start import start_handler, start_menu_callback, workflow_handler
from state import bot_status


async def _health(req):
    up = int((datetime.now() - bot_status["start_time"]).total_seconds())
    return web.json_response({"status": "healthy", "uptime_seconds": up, "running": bot_status["running"], "messages": bot_status["message_count"], "version": "11.4.0"})


async def _ping(req):
    return web.json_response({"pong": True, "ts": datetime.now().isoformat()})


async def start_http(port):
    aio = web.Application()
    aio.router.add_get("/", _ping)
    aio.router.add_get("/health", _health)
    aio.router.add_get("/ping", _ping)
    runner = web.AppRunner(aio)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"HTTP server up on 0.0.0.0:{port}")
    return runner


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut, Forbidden)):
        return
    if isinstance(err, Conflict):
        # Expected for a few seconds during a Render rolling deploy, when the
        # old and new instance both briefly poll with the same bot token.
        # PTB retries internally — just log it quietly, don't alert/crash.
        logger.warning("[Conflict] Another getUpdates instance is polling — will retry automatically.")
        return
    if isinstance(err, RetryAfter):
        await asyncio.sleep(err.retry_after + 1); return
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return
    bot_status["error_count"] += 1
    logger.error(f"[Err] {err}")
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=T.error_msg_1(err), parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


async def main():
    logger.info("STARTING BELUGA BOT v11.4.0")
    http_runner = await start_http(HTTP_PORT)
    await asyncio.sleep(0.3)

    app = TGApp.builder().token(BOT_TOKEN).build()

    await load_persistent_data()

    # Maintenance gate: group -10 runs before every other handler. While /shutdown is
    # active it swallows all updates (only the owner's /restart gets through).
    app.add_handler(TypeHandler(Update, maintenance_gate), group=-10)
    app.add_handler(CommandHandler("shutdown", shutdown_handler))
    app.add_handler(CommandHandler("restart", restart_handler))

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("workflow", workflow_handler))
    app.add_handler(CommandHandler("price", crypto_price_handler))
    app.add_handler(CommandHandler(["topgainers", "toplosers"], crypto_movers_handler))
    app.add_handler(CommandHandler(["chart", "chart5m", "chart15m", "chart1h", "chart4h", "chart1d"], crypto_chart_handler))
    app.add_handler(CommandHandler("news", lambda u, c: execute_news_flow(u, c, "crypto", "Crypto News")))
    app.add_handler(CommandHandler("ainews", lambda u, c: execute_news_flow(u, c, "ai", "AI News")))
    app.add_handler(CommandHandler("technews", lambda u, c: execute_news_flow(u, c, "tech", "Tech News")))
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("ss", ss_handler))
    app.add_handler(CommandHandler("refreshstickers", refreshstickers_handler))
    app.add_handler(CommandHandler("bananalogic", bananalogic_handler))
    app.add_handler(CommandHandler("qr", qr_generate_handler))
    app.add_handler(CommandHandler("scanqr", qr_scan_handler))
    app.add_handler(CommandHandler("watermark", watermark_handler))
    app.add_handler(CommandHandler("quiz", quiz_handler))
    app.add_handler(CommandHandler(["lb", "leaderboard"], lb_handler))
    app.add_handler(CommandHandler("nw", nw_handler))
    app.add_handler(CommandHandler(["pump", "dump"], pump_dump_handler))
    app.add_handler(CommandHandler("tictac", tictac_handler))
    app.add_handler(CommandHandler("mine", mine_handler))
    app.add_handler(CommandHandler("gm", gm_handler))
    app.add_handler(CommandHandler(["gay", "couple"], fun_dispatcher))
    app.add_handler(CommandHandler("clearmemory", clearmemory_handler))
    app.add_handler(CommandHandler("ping", ping_handler))
    app.add_handler(CommandHandler("model", model_command_handler))
    app.add_handler(CommandHandler("mute", mute_handler))
    app.add_handler(CommandHandler("ban", ban_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("shop", shop_handler))
    app.add_handler(CommandHandler("mytag", mytag_handler))
    app.add_handler(CommandHandler("peace", peace_handler))

    app.add_handler(CallbackQueryHandler(ttt_callback, pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(gm_callback, pattern=r"^gm:"))
    app.add_handler(CallbackQueryHandler(mine_callback, pattern=r"^mine:"))
    app.add_handler(CallbackQueryHandler(watermark_callback, pattern=r"^wm:"))
    app.add_handler(CallbackQueryHandler(model_callback, pattern=r"^model:"))
    app.add_handler(CallbackQueryHandler(start_menu_callback, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(antiraid_prevent_callback, pattern=r"^antiraid:"))
    app.add_handler(CallbackQueryHandler(shop_buy_callback, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(peace_callback, pattern=r"^peace:"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    app.add_handler(TypeHandler(Update, guest_message_handler), group=-1)
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_handler), group=0)
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, image_understanding_handler), group=0)
    app.add_handler(MessageHandler(filters.Sticker.ALL & filters.ChatType.GROUPS, monitor_group), group=0)
    # Sticker vision runs in its OWN group (not 0), so it doesn't shadow
    # monitor_group's sticker moderation above — PTB only runs the first
    # filter-match per group, and both need every group sticker to reach them.
    # Separate groups let both fire independently on the same update.
    app.add_handler(MessageHandler(filters.Sticker.ALL, image_understanding_handler), group=-2)
    # Catches "reply to someone else's photo/video/sticker with a question"
    # before the plain-text chat handlers (groups 1-3) would otherwise answer
    # generically without ever looking at the media being asked about.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, media_reply_question_handler), group=0)
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_reply_handler), group=4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, monitor_private_chat), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, monitor_ghost_mode), group=2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, monitor_group), group=3)

    app.add_error_handler(error_handler)

    await app.initialize()
    await app.start()

    await load_sticker_pack(app.bot, STICKER_PACK_MAIN)
    await load_sticker_pack(app.bot, STICKER_PACK_SAFE)
    await load_sticker_pack(app.bot, STICKER_PACK_ALT)
    await load_sticker_pack(app.bot, STICKER_PACK_WELCOME)
    await save_all_data()

    try:
        me = await app.bot.get_me()
        bot_status["username"] = me.username.lower()
        logger.info(f"Bot identity: @{me.username}")
    except Exception as e:
        logger.warning(f"[Startup get_me] {e}")

    # Safety: explicitly drop any stale webhook + pending updates before
    # polling starts, so an old instance's leftover session can't cause a
    # getUpdates Conflict with this one.
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"[Startup delete_webhook] {e}")

    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "edited_message", "callback_query", "poll_answer", "my_chat_member", "guest_message"],
    )
    bot_status["running"] = True
    logger.info("Beluga Bot is running")

    stop_evt = asyncio.Event()
    try:
        import signal
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop_evt.set)
        loop.add_signal_handler(signal.SIGINT, stop_evt.set)
    except Exception:
        pass

    cleanup_task = asyncio.create_task(cleanup_expired_games())
    sync_task = asyncio.create_task(periodic_sync())
    exchange_task = asyncio.create_task(init_exchange_async())
    revival_task = asyncio.create_task(revival_loop(app))
    cleanup_mongo_task = asyncio.create_task(mongo_cleanup_loop())

    await stop_evt.wait()
    logger.info("Shutting down...")
    cleanup_task.cancel()
    exchange_task.cancel()
    sync_task.cancel()
    bot_status["running"] = False
    for fn in [app.updater.stop, app.stop, app.shutdown, http_runner.cleanup]:
        try:
            await fn()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Conflict:
        # Another instance (usually the previous Render deploy, briefly
        # overlapping during a rolling deploy) was polling with the same
        # token. This is transient — exit cleanly so Render restarts this
        # instance rather than logging it as a hard crash.
        logger.warning("[Startup] Conflict: another instance was polling. Exiting to let Render restart cleanly.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal: {e}")
        sys.exit(1)
