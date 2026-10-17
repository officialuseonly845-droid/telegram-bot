"""/tictac and /mine."""
import asyncio
import random
import time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import logger
from database import sync_leaderboard_chat_to_mongo
from state import (
    game_timers,
    mine_games,
    mine_play_stats,
    mine_timers,
    ttt_games,
    user_in_game,
)
from styles import (
    MINE_IMAGE_URL,
    TTT_EMPTY,
    TTT_O,
    TTT_X,
    _mine_board_keyboard,
    _mine_setup_keyboard,
    mine_build_text,
    ttt_build_keyboard,
    ttt_build_text,
)
from utils import bump_score, game_key


WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]


def ttt_check_winner(board):
    for a, b, cc in WINS:
        if board[a] == board[b] == board[cc] and board[a] != TTT_EMPTY:
            return board[a]
    return None


def ttt_is_draw(board):
    return all(c != TTT_EMPTY for c in board) and not ttt_check_winner(board)


def _minimax(board, is_max, alpha, beta):
    w = ttt_check_winner(board)
    if w == TTT_O: return 10
    if w == TTT_X: return -10
    if all(c != TTT_EMPTY for c in board): return 0
    best = -1000 if is_max else 1000
    for i in range(9):
        if board[i] != TTT_EMPTY:
            continue
        board[i] = TTT_O if is_max else TTT_X
        score = _minimax(board, not is_max, alpha, beta)
        board[i] = TTT_EMPTY
        if is_max:
            best = max(best, score); alpha = max(alpha, best)
        else:
            best = min(best, score); beta = min(beta, best)
        if beta <= alpha:
            break
    return best


def ttt_bot_move(board):
    best_score, best_move = -1000, -1
    for i in range(9):
        if board[i] != TTT_EMPTY:
            continue
        board[i] = TTT_O
        score = _minimax(board, False, -1000, 1000)
        board[i] = TTT_EMPTY
        if score > best_score:
            best_score, best_move = score, i
    return best_move


async def cleanup_expired_games():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for gkey in list(ttt_games.keys()):
            g = ttt_games[gkey]
            if now - g.get("created", now) > 300:
                for uid in [str(g.get("x_id", "")), str(g.get("o_id", ""))]:
                    user_in_game.pop(uid, None)
                game_timers.pop(gkey, None)
                del ttt_games[gkey]


async def run_game_timer(c, gkey):
    try:
        g, td = ttt_games.get(gkey), game_timers.get(gkey)
        if not g or not td:
            return
        deadline = time.monotonic() + td.get("remaining", 60)
        last_shown = None
        while True:
            g, td = ttt_games.get(gkey), game_timers.get(gkey)
            if not g or not td or g.get("status") != "playing":
                return
            remaining = max(0, round(deadline - time.monotonic()))
            td["remaining"] = remaining
            cid, msg_id = g.get("chat_id"), g.get("msg_id")
            if not msg_id:
                return

            if remaining <= 0:
                g["status"] = "timeout"
                g["winner_name"] = (g["o_name"] if g["turn"] == "X" else g["x_name"])
                try:
                    await c.bot.edit_message_text(chat_id=cid, message_id=msg_id, text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"], disabled=True))
                except Exception:
                    pass
                for uid in [str(g.get("x_id", "")), str(g.get("o_id", ""))]:
                    user_in_game.pop(uid, None)
                game_timers.pop(gkey, None); ttt_games.pop(gkey, None)
                return

            if remaining != last_shown and remaining % 5 == 0:
                try:
                    await c.bot.edit_message_text(chat_id=cid, message_id=msg_id, text=ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(g["board"]))
                except Exception:
                    pass
                last_shown = remaining
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass


def player_busy(uid):
    gkey = user_in_game.get(uid)
    if not gkey:
        return False
    if gkey in ttt_games:
        return True
    user_in_game.pop(uid, None)
    return False


async def tictac_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        ua, cid, uid_a = u.effective_user, u.effective_chat.id, str(u.effective_user.id)
        name_a = (ua.first_name or "Player")[:20]
        vs_bot, user_b_id, name_b = True, None, "🤖 Bot"
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            rb = u.message.reply_to_message.from_user
            if not rb.is_bot:
                vs_bot, user_b_id, name_b = False, rb.id, (rb.first_name or "Player2")[:20]
                if player_busy(str(rb.id)):
                    await u.message.reply_text(T.TICTAC_MSG_1); return
        if player_busy(uid_a):
            await u.message.reply_text(T.TICTAC_MSG_2); return
        board = [TTT_EMPTY] * 9
        g = {"board": board, "turn": "X", "x_id": ua.id, "x_name": name_a, "o_id": user_b_id if not vs_bot else -1,
             "o_name": name_b, "vs_bot": vs_bot, "status": "playing", "created": time.time(), "chat_id": cid, "msg_id": None}
        msg = await u.message.reply_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        g["msg_id"] = msg.message_id
        gkey = game_key(msg.message_id, cid)
        ttt_games[gkey] = g
        game_timers[gkey] = {"remaining": 300}
        user_in_game[uid_a] = gkey
        if not vs_bot:
            user_in_game[str(user_b_id)] = gkey
        asyncio.create_task(run_game_timer(c, gkey))
    except Exception as e:
        logger.error(f"[tictac] {e}")


async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        try: await q.answer()
        except Exception: pass
        parts = q.data.split(":")
        action, idx = parts[1], int(parts[2]) if len(parts) > 2 else -1
        cid, mid = q.message.chat_id, q.message.message_id
        gkey = game_key(mid, cid)
        g = ttt_games.get(gkey)
        if not g or g["status"] != "playing" or action == "noop":
            return
        uid = str(q.from_user.id)
        if g["turn"] == "X" and uid != str(g["x_id"]): return
        if g["turn"] == "O" and not g["vs_bot"] and uid != str(g["o_id"]): return
        board = g["board"]
        if idx < 0 or idx >= 9 or board[idx] != TTT_EMPTY:
            return
        if gkey in game_timers:
            game_timers[gkey]["remaining"] = 300
        board[idx] = TTT_X if g["turn"] == "X" else TTT_O
        ws = ttt_check_winner(board)
        if ws:
            g["status"], g["winner_name"] = "win", (g["x_name"] if ws == TTT_X else g["o_name"])
            winner_uid = str(g["x_id"]) if ws == TTT_X else str(g["o_id"] if not g["vs_bot"] else -1)
            if winner_uid and winner_uid != "-1":
                bump_score(str(cid), winner_uid, g["winner_name"], +10)
                await sync_leaderboard_chat_to_mongo(str(cid))
            try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except Exception: pass
            for uid in [str(g["x_id"]), str(g["o_id"])]: user_in_game.pop(uid, None)
            game_timers.pop(gkey, None); ttt_games.pop(gkey, None); return
        if ttt_is_draw(board):
            g["status"] = "draw"
            try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
            except Exception: pass
            for uid in [str(g["x_id"]), str(g["o_id"])]: user_in_game.pop(uid, None)
            game_timers.pop(gkey, None); ttt_games.pop(gkey, None); return
        g["turn"] = "O" if g["turn"] == "X" else "X"
        if g["vs_bot"] and g["turn"] == "O":
            bi = ttt_bot_move(board)
            if bi >= 0:
                board[bi] = TTT_O
                ws2 = ttt_check_winner(board)
                if ws2 or ttt_is_draw(board):
                    g["status"] = "win" if ws2 else "draw"
                    if ws2: g["winner_name"] = (g["x_name"] if ws2 == TTT_X else g["o_name"])
                    try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board, disabled=True))
                    except Exception: pass
                    user_in_game.pop(str(g["x_id"]), None)
                    game_timers.pop(gkey, None); ttt_games.pop(gkey, None); return
                g["turn"] = "X"
        try: await q.edit_message_text(ttt_build_text(g), parse_mode=ParseMode.MARKDOWN, reply_markup=ttt_build_keyboard(board))
        except Exception: pass
    except Exception as e:
        logger.error(f"[ttt_cb] {e}")


async def mine_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        cid, uid = str(u.effective_chat.id), str(u.effective_user.id)
        now = time.time()
        m_stat = mine_play_stats.setdefault(uid, {"plays": 0, "block_until": 0})
        if now < m_stat["block_until"]:
            rem_m = max(1, int((m_stat["block_until"] - now) // 60))
            await u.message.reply_text(T.mine_msg_1(rem_m), parse_mode=ParseMode.MARKDOWN); return
        m_stat["plays"] += 1
        if m_stat["plays"] > 20:
            m_stat["block_until"] = now + 3600; m_stat["plays"] = 0
            await u.message.reply_text(T.MINE_MSG_2, parse_mode=ParseMode.MARKDOWN); return
        gkey = f"{cid}_{uid}_{int(now)}"
        mine_games[gkey] = {"uid": uid, "name": (u.effective_user.first_name or "Player")[:20], "bombs": 0,
                             "state": [], "revealed": [False]*6, "chat_id": u.effective_chat.id, "msg_id": None, "status": "setting"}
        msg = await u.message.reply_photo(photo=MINE_IMAGE_URL, caption=T.MINE_MSG_3, parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_setup_keyboard(gkey))
        mine_games[gkey]["msg_id"] = msg.message_id
    except Exception as e:
        logger.error(f"[mine] {e}")


async def run_mine_timer(c, gkey):
    try:
        while True:
            await asyncio.sleep(5)
            g, td = mine_games.get(gkey), mine_timers.get(gkey)
            if not g or not td or g.get("status") != "playing":
                return
            td["remaining"] = max(0, td["remaining"] - 5)
            cid, msg_id = g.get("chat_id"), g.get("msg_id")
            if not msg_id:
                return
            if td["remaining"] <= 0:
                g["status"] = "timeout"
                new_sc = bump_score(str(cid), g["uid"], g["name"], -5)
                await sync_leaderboard_chat_to_mongo(str(cid))
                try:
                    await c.bot.edit_message_caption(chat_id=cid, message_id=msg_id, caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True))
                except Exception:
                    pass
                mine_timers.pop(gkey, None); mine_games.pop(gkey, None)
                return
            try:
                await c.bot.edit_message_caption(chat_id=cid, message_id=msg_id, caption=mine_build_text(g, td["remaining"]), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"]))
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


async def mine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        try: await q.answer()
        except Exception: pass
        parts = q.data.split(":")
        action, gkey, val = parts[1], parts[2], int(parts[3])
        if gkey not in mine_games:
            return
        g = mine_games[gkey]
        if str(q.from_user.id) != g["uid"]:
            await q.answer(T.MINE_MSG_4); return
        if action == "noop":
            return
        if action == "set":
            if g.get("status") != "setting": return
            bombs = max(3, min(5, val))
            state = [True]*bombs + [False]*(6-bombs)
            random.shuffle(state)
            g.update({"bombs": bombs, "state": state, "status": "playing", "revealed": [False]*6})
            mine_timers[gkey] = {"remaining": 60}
            asyncio.create_task(run_mine_timer(context, gkey))
            try: await q.edit_message_caption(caption=mine_build_text(g, 60), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, state, g["revealed"]))
            except Exception: pass
        elif action == "play":
            if g.get("status") != "playing" or g["revealed"][val]:
                return
            is_bomb = g["state"][val]
            cid = str(q.message.chat_id)
            if is_bomb:
                g["status"] = "lost"
                mine_timers.pop(gkey, None)
                new_sc = bump_score(cid, g["uid"], g["name"], -5)
                await sync_leaderboard_chat_to_mongo(cid)
                try: await q.edit_message_caption(caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True))
                except Exception: pass
                mine_games.pop(gkey, None)
            else:
                g["revealed"][val] = True
                total_safe = 6 - g["bombs"]
                opened_count = sum(1 for x in g["revealed"] if x)
                if gkey in mine_timers:
                    mine_timers[gkey]["remaining"] = 60
                if opened_count >= total_safe:
                    g["status"] = "won"
                    mine_timers.pop(gkey, None)
                    new_sc = bump_score(cid, g["uid"], g["name"], +700)
                    await sync_leaderboard_chat_to_mongo(cid)
                    try: await q.edit_message_caption(caption=mine_build_text(g, 0) + f"\n\nBalance: *{new_sc:,} pts*", parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"], disabled=True))
                    except Exception: pass
                    mine_games.pop(gkey, None)
                else:
                    rem = mine_timers.get(gkey, {}).get("remaining", 60)
                    try: await q.edit_message_caption(caption=mine_build_text(g, rem), parse_mode=ParseMode.MARKDOWN, reply_markup=_mine_board_keyboard(gkey, g["state"], g["revealed"]))
                    except Exception: pass
    except Exception as e:
        logger.error(f"[mine_callback] {e}")
