"""
trades.py — BTC / Gold futures scalping signal engine (Binance free public data, Telegram delivery)

What it does
  1. Pulls 5m candles from Binance public REST (no API key).
  2. Two rule-based strategy families (momentum breakout, trend pullback) -> LONG / SHORT signals
     with entry, stop-loss, take-profit and a plain-language explanation.
  3. Backtester (fees + slippage, SL-first on ambiguous candles, next-candle-open entries, no look-ahead)
     + walk-forward optimizer: picks parameters on the first 60% of history and checks them on the unseen 40%.
  4. Paper trading: every signal opens a virtual position, tracked on 1m candles until SL / TP / time stop.
  5. Auto-scanner: looks for setups 24/7 and DMs them to OWNER_ID only. Everything is owner-only.

Command (owner only, silently ignored for everybody else)
  /analytics btc|xau   everything in one place: signal right now (entry / SL / TP / why), every trade taken so far
                       (win rate, net gain, open trade status), and the automatic backtest results.
Backtesting runs by itself in the background (start + every 6h). Signals, trade results (TP / SL hit), backtests
and the liquidity-heatmap history are stored in MongoDB (local file fallback), so deploys lose nothing.

Wiring (bot.py)
  from handlers.trades import analytics_handler, start_trades_engine
  app.add_handler(CommandHandler("analytics", analytics_handler))
  trades_task = start_trades_engine(app.bot)      # once, inside the running event loop

No live order execution on purpose: run paper trading first. Not financial advice.
"""
import asyncio
import json
import math
import os
import time
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import OWNER_ID, logger
import handlers.crypto as _cr
from handlers.crypto import BRAND, _fetch_klines_range, _fetch_ohlcv_sync, _fmt_price, _log_exc, _mdb

# ----------------------------------------------------------------------------- settings
TF = "5m"
TF_MS = 300_000
SYMBOLS = {
    # min_tp = minimum take-profit distance in USD. BTC: $400. Gold: 300 pips = $30 (1 pip = $0.10).
    "BTC": {"name": "BTC/USDT", "min_tp": 400.0, "tick": 0.1, "pip": None},
    "XAU": {"name": "XAU/USDT (gold)", "min_tp": 30.0, "tick": 0.01, "pip": 0.10},
}
ALIASES = {"BTC": "BTC", "BITCOIN": "BTC", "XAU": "XAU", "GOLD": "XAU", "XAUUSD": "XAU", "PAXG": "XAU"}

FEE = 0.0004             # taker fee per side (futures)
SLIP = 0.0001            # slippage per side
RISK = 0.01              # risk per trade, fraction of equity
LEV_CAP = 10.0           # max leverage used when sizing
MAX_HOLD_BARS = 96       # time stop: 96 x 5m = 8h
COOLDOWN_BARS = 6        # bars to wait after a trade closes
WARMUP = 210             # bars needed before EMA200 etc. are valid
HISTORY_DAYS = 45
TRAIN_FRAC = 0.60
MIN_TRAIN_TRADES = 12
VALID_MIN_TEST_TRADES = 8
VALID_MIN_PF = 1.15
VALID_MIN_EXP = 0.05     # expectancy in R on unseen data
SEND_UNVALIDATED = False  # True = also DM signals from configs that failed validation (not recommended)
SCAN_EVERY = 30          # seconds between scanner loops
OPTIMIZE_EVERY = 6 * 3600
PAPER_START = 1000.0
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades_state.json")

DEFAULT_CFG = {"family": "A", "n": 48, "vol": 1.2, "k_sl": 1.8, "k_tp": 4.0, "min_atr_f": 0.0}

_state = {"cfg": {}, "paper": None, "hist": {}}
_engine_task = None
_opt_lock = None


# ----------------------------------------------------------------------------- indicators
def _ema(v, n):
    out = [None] * len(v)
    if len(v) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(v[:n]) / n
    out[n - 1] = e
    for i in range(n, len(v)):
        e = v[i] * k + e * (1 - k)
        out[i] = e
    return out


def _sma(v, n):
    out = [None] * len(v)
    s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / n, l / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def _atr(h, l, c, n=14):
    out = [None] * len(c)
    if len(c) < n:
        return out
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[:n]) / n
    out[n - 1] = a
    for i in range(n, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


class Ind:
    """All indicators for a candle list. Every value at index i only uses candles <= i (no look-ahead)."""

    def __init__(self, rows):
        self.t = [r[0] for r in rows]
        self.o = [r[1] for r in rows]
        self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]
        self.c = [r[4] for r in rows]
        self.v = [r[5] for r in rows]
        self.ema21 = _ema(self.c, 21)
        self.ema50 = _ema(self.c, 50)
        self.ema200 = _ema(self.c, 200)
        self.rsi = _rsi(self.c, 14)
        self.atr = _atr(self.h, self.l, self.c, 14)
        self.vsma = _sma(self.v, 20)
        self._don = {}

    def don(self, n):
        """Donchian channel of the PREVIOUS n candles (current candle excluded)."""
        d = self._don.get(n)
        if d is None:
            hi, lo = [None] * len(self.c), [None] * len(self.c)
            for i in range(n, len(self.c)):
                hi[i] = max(self.h[i - n:i])
                lo[i] = min(self.l[i - n:i])
            d = self._don[n] = (hi, lo)
        return d


# ----------------------------------------------------------------------------- strategies
def _sig_breakout(ind, i, p, min_tp):
    """Family A — momentum breakout in the direction of the trend."""
    a, e50, e200, e21 = ind.atr[i], ind.ema50[i], ind.ema200[i], ind.ema21[i]
    vs, r = ind.vsma[i], ind.rsi[i]
    if None in (a, e50, e200, e21, vs, r) or a <= 0 or vs <= 0:
        return None
    if a < p["min_atr_f"] * min_tp:
        return None
    vr = ind.v[i] / vs
    if vr < p["vol"]:
        return None
    o, h, l, c = ind.o[i], ind.h[i], ind.l[i], ind.c[i]
    rng = h - l
    hh, ll = ind.don(p["n"])[0][i], ind.don(p["n"])[1][i]
    if rng <= 0 or hh is None:
        return None
    sl_d = p["k_sl"] * a
    tp_d = max(min_tp, p["k_tp"] * a)
    if tp_d / sl_d < 1.3:
        return None
    if e50 > e200 and c > e50 and c > hh and 55 <= r <= 78 and (c - e21) <= 2.2 * a and (c - l) / rng >= 0.65:
        return {"side": "LONG", "sl_d": sl_d, "tp_d": tp_d, "atr": a, "rsi": r, "reasons": [
            "Trend UP: EMA50 above EMA200 and price above EMA50",
            f"Breakout: close {_fmt_price(c)} above the {p['n']}-candle high {_fmt_price(hh)}",
            f"Momentum: RSI {r:.0f}, candle closed near its high",
            f"Volume {vr:.1f}x the 20-candle average",
            f"ATR {_fmt_price(a)}: stop {p['k_sl']}x ATR, target at least the minimum move"]}
    if e50 < e200 and c < e50 and c < ll and 22 <= r <= 45 and (e21 - c) <= 2.2 * a and (h - c) / rng >= 0.65:
        return {"side": "SHORT", "sl_d": sl_d, "tp_d": tp_d, "atr": a, "rsi": r, "reasons": [
            "Trend DOWN: EMA50 below EMA200 and price below EMA50",
            f"Breakdown: close {_fmt_price(c)} below the {p['n']}-candle low {_fmt_price(ll)}",
            f"Momentum: RSI {r:.0f}, candle closed near its low",
            f"Volume {vr:.1f}x the 20-candle average",
            f"ATR {_fmt_price(a)}: stop {p['k_sl']}x ATR, target at least the minimum move"]}
    return None


def _sig_pullback(ind, i, p, min_tp):
    """Family B — buy the dip / sell the rally inside an established trend."""
    a, e50, e200 = ind.atr[i], ind.ema50[i], ind.ema200[i]
    e21, e21p = ind.ema21[i], ind.ema21[i - 1]
    vs, r, rp = ind.vsma[i], ind.rsi[i], ind.rsi[i - 1]
    if None in (a, e50, e200, e21, e21p, vs, r, rp) or a <= 0 or vs <= 0:
        return None
    if a < p["min_atr_f"] * min_tp:
        return None
    vr = ind.v[i] / vs
    if vr < p["vol"]:
        return None
    o, h, l, c = ind.o[i], ind.h[i], ind.l[i], ind.c[i]
    ph, pl = ind.h[i - 1], ind.l[i - 1]
    if e50 > e200 and c > e21 and c > o and c > ph:
        if min(ind.l[i - 3:i]) <= e21p + p["touch"] * a and p["rsi_lo"] <= r <= 68 and r > rp:
            swing = min(ind.l[i - 4:i + 1])
            sl_d = min(max(c - swing + 0.2 * a, a), 3 * a)
            tp_d = max(min_tp, p["rr"] * sl_d)
            if tp_d / sl_d < 1.3:
                return None
            return {"side": "LONG", "sl_d": sl_d, "tp_d": tp_d, "atr": a, "rsi": r, "reasons": [
                "Trend UP: EMA50 above EMA200",
                f"Pullback: price dipped to the EMA21 area ({_fmt_price(e21p)}) in the last 3 candles",
                f"Trigger: bullish candle closed above the previous high {_fmt_price(ph)}",
                f"RSI {r:.0f} and rising (momentum turning back up)",
                f"Stop under the pullback low, target {p['rr']}R or the minimum move"]}
    if e50 < e200 and c < e21 and c < o and c < pl:
        if max(ind.h[i - 3:i]) >= e21p - p["touch"] * a and 32 <= r <= 100 - p["rsi_lo"] and r < rp:
            swing = max(ind.h[i - 4:i + 1])
            sl_d = min(max(swing - c + 0.2 * a, a), 3 * a)
            tp_d = max(min_tp, p["rr"] * sl_d)
            if tp_d / sl_d < 1.3:
                return None
            return {"side": "SHORT", "sl_d": sl_d, "tp_d": tp_d, "atr": a, "rsi": r, "reasons": [
                "Trend DOWN: EMA50 below EMA200",
                f"Pullback: price rallied to the EMA21 area ({_fmt_price(e21p)}) in the last 3 candles",
                f"Trigger: bearish candle closed below the previous low {_fmt_price(pl)}",
                f"RSI {r:.0f} and falling (momentum turning back down)",
                f"Stop above the rally high, target {p['rr']}R or the minimum move"]}
    return None


def signal_at(ind, i, cfg, min_tp):
    if i < WARMUP:
        return None
    return _sig_breakout(ind, i, cfg, min_tp) if cfg["family"] == "A" else _sig_pullback(ind, i, cfg, min_tp)


def cfg_name(cfg):
    if cfg["family"] == "A":
        s = f"Momentum Breakout ({cfg['n']}-bar high, vol>={cfg['vol']}x, SL {cfg['k_sl']}xATR, TP {cfg['k_tp']}xATR"
    else:
        s = f"Trend Pullback (RR {cfg['rr']}, touch {cfg['touch']}xATR, RSI>={cfg['rsi_lo']}, vol>={cfg['vol']}x"
    return s + (f", min ATR {cfg['min_atr_f']}x target)" if cfg["min_atr_f"] else ")")


def _grid():
    out = []
    for n in (24, 48):
        for vol in (1.0, 1.4):
            for k_sl in (1.5, 2.2):
                for k_tp in (3.0, 4.5):
                    for f in (0.0, 0.25):
                        out.append({"family": "A", "n": n, "vol": vol, "k_sl": k_sl, "k_tp": k_tp, "min_atr_f": f})
    for rr in (1.5, 2.0, 2.5):
        for touch in (0.2, 0.5):
            for rsi_lo in (40, 48):
                for vol in (0.0, 0.9):
                    for f in (0.0, 0.25):
                        out.append({"family": "B", "rr": rr, "touch": touch, "rsi_lo": rsi_lo, "vol": vol, "min_atr_f": f})
    return out


# ----------------------------------------------------------------------------- backtester
def run_backtest(ind, cfg, min_tp, lo_i, hi_i):
    """Simulates trades on bars [lo_i, hi_i). Entry = NEXT candle open (+slippage). If SL and TP are both
    touched inside one candle the SL is assumed to hit first. Time stop after MAX_HOLD_BARS."""
    trades = []
    i = max(lo_i, WARMUP)
    while i < hi_i - 1:
        sig = signal_at(ind, i, cfg, min_tp)
        if not sig:
            i += 1
            continue
        long = sig["side"] == "LONG"
        ei = i + 1
        entry = ind.o[ei] * (1 + SLIP if long else 1 - SLIP)
        sl = entry - sig["sl_d"] if long else entry + sig["sl_d"]
        tp = entry + sig["tp_d"] if long else entry - sig["tp_d"]
        last = min(hi_i - 1, ei + MAX_HOLD_BARS)
        j, exit_px, why = ei, None, "TIME"
        while j <= last:
            hit_sl = ind.l[j] <= sl if long else ind.h[j] >= sl
            hit_tp = ind.h[j] >= tp if long else ind.l[j] <= tp
            if hit_sl:
                exit_px, why = sl, "SL"
                break
            if hit_tp:
                exit_px, why = tp, "TP"
                break
            j += 1
        if exit_px is None:
            j = last
            exit_px = ind.c[j]
        gross = (exit_px - entry) if long else (entry - exit_px)
        net = gross - entry * (2 * FEE + SLIP)
        trades.append({"i": ei, "j": j, "side": sig["side"], "entry": entry, "exit": exit_px, "why": why,
                       "R": net / sig["sl_d"], "usd": net, "sl_pct": sig["sl_d"] / entry, "bars": j - ei + 1})
        i = j + COOLDOWN_BARS
    return trades


def metrics(trades, n_bars):
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0, "ret": 0.0, "dd": 0.0, "tpd": 0.0, "avg_usd": 0.0}
    gw = sum(t["R"] for t in trades if t["R"] > 0)
    gl = -sum(t["R"] for t in trades if t["R"] < 0)
    pf = 99.0 if gl == 0 and gw > 0 else (gw / gl if gl > 0 else 0.0)
    eq, peak, dd = 1.0, 1.0, 0.0
    for t in trades:
        eq *= 1 + min(RISK, LEV_CAP * t["sl_pct"]) * t["R"]
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    days = max(n_bars * TF_MS / 86_400_000, 1e-9)
    return {"n": n, "wr": sum(1 for t in trades if t["R"] > 0) / n * 100, "pf": min(pf, 99.0),
            "exp": sum(t["R"] for t in trades) / n, "ret": (eq - 1) * 100, "dd": dd * 100,
            "tpd": n / days, "avg_usd": sum(t["usd"] for t in trades) / n}


def optimize(rows, sym):
    """Walk-forward: choose the best config on the first 60% of the data, then judge it on the unseen 40%."""
    min_tp = SYMBOLS[sym]["min_tp"]
    ind = Ind(rows)
    n = len(rows)
    split = int(n * TRAIN_FRAC)
    best = None
    tested = 0
    for cfg in _grid():
        tr = run_backtest(ind, cfg, min_tp, WARMUP, split)
        m = metrics(tr, split - WARMUP)
        tested += 1
        if m["n"] < MIN_TRAIN_TRADES or m["exp"] <= 0:
            continue
        score = m["exp"] * math.sqrt(m["n"])
        if best is None or score > best[0]:
            best = (score, cfg, m)
    if best is None:
        return {"cfg": None, "validated": False, "tested": tested, "n_bars": n,
                "reason": "No configuration was profitable on the training period with enough trades."}
    _, cfg, train_m = best
    test_m = metrics(run_backtest(ind, cfg, min_tp, split, n), n - split)
    validated = (test_m["n"] >= VALID_MIN_TEST_TRADES and test_m["pf"] >= VALID_MIN_PF and test_m["exp"] >= VALID_MIN_EXP)
    reason = "OK" if validated else (
        f"Failed on unseen data (trades {test_m['n']}, PF {test_m['pf']:.2f}, expectancy {test_m['exp']:+.2f}R).")
    return {"cfg": cfg, "train": train_m, "test": test_m, "validated": validated, "reason": reason,
            "tested": tested, "n_bars": n, "ts": time.time(),
            "period": f"{rows[0][0]} - {rows[-1][0]}"}


# ----------------------------------------------------------------------------- state / persistence
# MongoDB (collections: trades_state, trades_log, trades_backtests) with a local JSON file as fallback.
def _new_paper():
    return {"equity": PAPER_START, "start": PAPER_START, "open": {}, "closed": [], "last_bar": {}, "cool": {}}


def _paper():
    if _state["paper"] is None:
        _state["paper"] = _new_paper()
    return _state["paper"]


def _active_cfg(sym):
    st = _state["cfg"].get(sym)
    if st and st.get("cfg"):
        return st["cfg"], bool(st.get("validated")), st
    return DEFAULT_CFG, False, st


def _file_save():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.warning(f"[trades] file save failed: {str(e)[:80]}")


def _file_load():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        _state["cfg"] = data.get("cfg", {})
        _state["hist"] = data.get("hist", {})
        _state["paper"] = {**_new_paper(), **(data.get("paper") or {})}
    except Exception:
        _state["paper"] = _new_paper()


async def _db_save_state():
    db = await _mdb()
    if db is None:
        return
    try:
        p = {k: v for k, v in _paper().items() if k != "closed"}      # closed trades live in trades_log
        await db["trades_state"].replace_one({"_id": "paper"}, {"_id": "paper", **p}, upsert=True)
        for sym, res in list(_state["cfg"].items()):
            await db["trades_state"].replace_one({"_id": f"cfg:{sym}"}, {"_id": f"cfg:{sym}", **res}, upsert=True)
    except Exception as e:
        logger.warning(f"[trades] mongo save failed: {str(e)[:100]}")


def _save_state():
    """Local file now + Mongo in the background."""
    _file_save()
    try:
        asyncio.get_running_loop().create_task(_db_save_state())
    except RuntimeError:
        pass


async def _db_log_trade(rec):
    db = await _mdb()
    if db is None:
        return
    try:
        await db["trades_log"].insert_one(dict(rec))
    except Exception as e:
        logger.warning(f"[trades] trade log failed: {str(e)[:100]}")


async def _record_backtest(sym, res):
    """Stores the latest backtest as the active config and appends it to the history (memory + Mongo)."""
    _state["cfg"][sym] = res
    summary = {"sym": sym, "ts": res.get("ts", time.time()), "validated": bool(res.get("validated")),
               "cfg": res.get("cfg"), "train": res.get("train"), "test": res.get("test"),
               "tested": res.get("tested"), "days": res.get("days")}
    h = _state["hist"].setdefault(sym, [])
    h.insert(0, summary)
    del h[10:]
    _save_state()
    db = await _mdb()
    if db is not None:
        try:
            await db["trades_backtests"].insert_one(dict(summary))
        except Exception as e:
            logger.warning(f"[trades] backtest log failed: {str(e)[:100]}")


async def _db_load():
    db = await _mdb()
    if db is None:
        return False
    try:
        async for d in db["trades_state"].find({}):
            key = str(d.pop("_id"))
            if key == "paper":
                _state["paper"] = {**_new_paper(), **d}
            elif key.startswith("cfg:"):
                _state["cfg"][key[4:]] = d
        closed = []
        async for d in db["trades_log"].find({}).sort("closed", 1):
            d.pop("_id", None)
            closed.append(d)
        _paper()["closed"] = closed[-2000:]
        for sym in SYMBOLS:
            hist = []
            async for d in db["trades_backtests"].find({"sym": sym}).sort("ts", -1).limit(10):
                d.pop("_id", None)
                hist.append(d)
            _state["hist"][sym] = hist
        logger.info(f"[trades] loaded from Mongo: {len(closed)} trades, {len(_state['cfg'])} backtest configs")
        return True
    except Exception as e:
        logger.warning(f"[trades] mongo load failed: {str(e)[:100]}")
        return False


# ----------------------------------------------------------------------------- formatting helpers
def _is_owner(u: Update) -> bool:
    uid = getattr(u.effective_user, "id", None)
    return bool(uid is not None and OWNER_ID and str(uid) == str(OWNER_ID))


def _sym_from(tokens):
    if not tokens:
        return "BTC"
    return ALIASES.get(tokens[0].upper().replace("USDT", "").replace("/", ""))


def _px(sym, x):
    return f"{x:,.2f}" if SYMBOLS[sym]["tick"] < 1 else f"{x:,.1f}"


def _usd(x):
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def _now_utc():
    return datetime.now(timezone.utc).strftime("%H:%M")


def _levels(sym, sig, entry):
    long = sig["side"] == "LONG"
    tick = SYMBOLS[sym]["tick"]
    sl = entry - sig["sl_d"] if long else entry + sig["sl_d"]
    tp = entry + sig["tp_d"] if long else entry - sig["tp_d"]
    return round(sl / tick) * tick, round(tp / tick) * tick


def _stats_line(st):
    if not st or not st.get("test"):
        return "Backtest : not run yet (use /backtest)"
    t = st["test"]
    tag = "validated" if st.get("validated") else "NOT validated"
    return (f"Backtest on unseen {int((1 - TRAIN_FRAC) * 100)}% of data ({tag}) : "
            f"{t['n']} trades, win rate {t['wr']:.0f}%, PF {t['pf']:.2f}, avg {t['exp']:+.2f}R")


def _signal_text(sym, sig, entry, st, validated, opened_paper):
    long = sig["side"] == "LONG"
    sl, tp = _levels(sym, sig, entry)
    cfg = (st or {}).get("cfg") or DEFAULT_CFG
    sl_d, tp_d = abs(entry - sl), abs(tp - entry)
    pip = SYMBOLS[sym]["pip"]
    tp_extra = f"  ({tp_d / pip:,.0f} pips)" if pip else ""
    why = "\n".join(f"• {r}" for r in sig["reasons"])
    head = "🟢 *LONG SIGNAL*" if long else "🔴 *SHORT SIGNAL*"
    warn = "" if validated else "\n⚠️ *Strategy not validated yet* — treat as analysis only.\n"
    paper = "📝 Paper trade opened (virtual, 1% risk).\n\n" if opened_paper else ""
    return (f"{head}\n`{SYMBOLS[sym]['name']}`  ·  `{TF}`  ·  `{_now_utc()} UTC`\n\n"
            f"📍 *Entry* :  `{_px(sym, entry)}`\n\n"
            f"🛑 *Stop Loss* :  `{_px(sym, sl)}`  ({_usd(-sl_d)} · {sl_d / entry * 100:.2f}%)\n\n"
            f"🎯 *Take Profit* :  `{_px(sym, tp)}`  ({_usd(tp_d)} · {tp_d / entry * 100:.2f}%){tp_extra}\n\n"
            f"⚖️ *Risk : Reward* :  `1 : {tp_d / sl_d:.1f}`\n\n"
            f"🧠 *Why*\n{why}\n\n"
            f"📊 *Strategy* :  {cfg_name(cfg)}\n{_stats_line(st)}\n{warn}\n"
            f"{paper}⚠️ Not financial advice. Paper trade first.\n\n{BRAND}")


# ----------------------------------------------------------------------------- Telegram helpers
async def _send(bot, chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
        return True
    except Exception as e:
        try:
            await bot.send_message(chat_id=chat_id, text=text.replace("*", "").replace("`", ""))
            return True
        except Exception:
            logger.warning(f"[trades] send failed: {str(e)[:100]}")
            return False


async def _to_owner(bot, text):
    return bool(OWNER_ID) and await _send(bot, int(OWNER_ID), text)


async def _reply_private(u: Update, c: ContextTypes.DEFAULT_TYPE, text):
    """Owner-only output. In groups the answer goes to the owner's DM, never into the group."""
    chat = u.effective_chat
    if chat is not None and chat.type == "private":
        await _send(c.bot, chat.id, text)
    else:
        ok = await _to_owner(c.bot, text)
        await u.message.reply_text("📩 DM check karo." if ok else "⚠️ Pehle bot ko DM me /start karo.")


# ----------------------------------------------------------------------------- live analysis
def _closed_candles(ticker, limit=500):
    rows, _src = _fetch_ohlcv_sync(ticker, TF, limit)
    if not rows:
        return None, None
    now_ms = int(time.time() * 1000)
    closed = [r for r in sorted(rows, key=lambda r: r[0]) if r[0] + TF_MS <= now_ms]
    return closed, rows[-1][4]


def _analyze_sync(sym):
    closed, price = _closed_candles(sym)
    if not closed or len(closed) < WARMUP + 5:
        return None
    ind = Ind(closed)
    i = len(closed) - 1
    cfg, validated, st = _active_cfg(sym)
    sig = signal_at(ind, i, cfg, SYMBOLS[sym]["min_tp"])
    return {"ind": ind, "i": i, "price": price, "cfg": cfg, "validated": validated, "st": st, "sig": sig,
            "bar_ts": closed[-1][0]}


def _analysis_text(sym, a):
    ind, i, price = a["ind"], a["i"], a["price"]
    e21, e50, e200 = ind.ema21[i], ind.ema50[i], ind.ema200[i]
    atr, rsi = ind.atr[i], ind.rsi[i]
    vr = ind.v[i] / ind.vsma[i] if ind.vsma[i] else 0.0
    trend = "UP 📈" if e50 > e200 else "DOWN 📉"
    bar_time = datetime.fromtimestamp(a["bar_ts"] / 1000, tz=timezone.utc).strftime("%H:%M")
    name = SYMBOLS[sym]["name"]
    if a["sig"]:
        body = _signal_text(sym, a["sig"], price, a["st"], a["validated"], opened_paper=False)
        busy = "ℹ️ Is market me tumhara ek trade already open hai (neeche dekho).\n\n" if sym in _paper()["open"] else ""
        return (f"🔎 *Analytics*  ·  `{name}`\n\n"
                f"💵 *Price* :  `{_px(sym, price)}`\n\n{busy}{body}")
    cfg = a["cfg"]
    if cfg["family"] == "A":
        hh, ll = ind.don(cfg["n"])[0][i], ind.don(cfg["n"])[1][i]
        trig = (f"🟢 Long above :  `{_px(sym, hh)}`  (5m close, needs trend UP)\n"
                f"🔴 Short below :  `{_px(sym, ll)}`  (5m close, needs trend DOWN)")
    else:
        trig = (f"🟢 Long : pullback to EMA21 `{_px(sym, e21)}`, then a bullish close above the previous high "
                f"`{_px(sym, ind.h[i - 1])}`\n"
                f"🔴 Short : rally to EMA21, then a bearish close below the previous low `{_px(sym, ind.l[i - 1])}`")
    quiet = "\n⚠️ Market quiet: ATR is small for a full-size target." if atr * 3 < SYMBOLS[sym]["min_tp"] else ""
    warn = "" if a["validated"] else "\n⚠️ Strategy abhi validated nahi hai, auto-DM band hai.\n"
    return (f"🔎 *Analytics*  ·  `{name}`\n\n"
            f"⚪ *NO TRADE right now*\n\n"
            f"💵 *Price* :  `{_px(sym, price)}`\n\n"
            f"📐 *Trend* :  {trend}  (EMA50 `{_px(sym, e50)}` · EMA200 `{_px(sym, e200)}`)\n"
            f"📊 *RSI* :  `{rsi:.0f}`   ·   *ATR* :  `{_px(sym, atr)}`   ·   *Volume* :  `{vr:.1f}x`{quiet}\n\n"
            f"🎯 *Waiting for*\n{trig}\n\n"
            f"📊 *Strategy* :  {cfg_name(cfg)}\n{_stats_line(a['st'])}\n{warn}\n"
            f"🕒 Last closed 5m candle: `{bar_time} UTC`")


def _when(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M")


def _rec_stats(tr):
    n = len(tr)
    if not n:
        return None
    gw = sum(t["R"] for t in tr if t["R"] > 0)
    gl = -sum(t["R"] for t in tr if t["R"] < 0)
    return {"n": n, "w": sum(1 for t in tr if t["R"] > 0),
            "tp": sum(1 for t in tr if t["why"] == "TP"), "sl": sum(1 for t in tr if t["why"] == "SL"),
            "tm": sum(1 for t in tr if t["why"] == "TIME"), "wr": sum(1 for t in tr if t["R"] > 0) / n * 100,
            "pf": (gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)), "avg": sum(t["R"] for t in tr) / n,
            "pnl": sum(t["pnl"] for t in tr), "moves": sum(t["usd_move"] for t in tr),
            "best": max(t["R"] for t in tr), "worst": min(t["R"] for t in tr)}


def _record_text(sym, price):
    p = _paper()
    name = SYMBOLS[sym]["name"]
    mine = [t for t in p["closed"] if t["sym"] == sym]
    st, al = _rec_stats(mine), _rec_stats(p["closed"])
    if st:
        body = (f"Trades :  `{st['n']}`   (🎯 {st['tp']} TP  ·  🛑 {st['sl']} SL  ·  ⏱ {st['tm']} time stop)\n\n"
                f"Win rate :  `{st['wr']:.0f}%`   ·   PF `{st['pf']:.2f}`   ·   Avg `{st['avg']:+.2f}R`\n\n"
                f"Net gain :  `{st['pnl']:+,.2f} USDT`   ·   price moves `{_usd(st['moves'])}`\n\n"
                f"Best `{st['best']:+.2f}R`   ·   Worst `{st['worst']:+.2f}R`")
    else:
        body = "Abhi tak is market ka koi trade close nahi hua."
    all_line = (f"All markets :  {al['n']} trades  ·  win rate {al['wr']:.0f}%  ·  net {al['pnl']:+,.2f} USDT"
                if al else "All markets :  no closed trades yet")
    ret = (p["equity"] / p["start"] - 1) * 100
    pos = p["open"].get(sym)
    if pos:
        long = pos["side"] == "LONG"
        now_txt, floating = "", ""
        if price:
            mv = (price - pos["entry"]) if long else (pos["entry"] - price)
            now_txt = f"\n💵 Now `{_px(sym, price)}`  ·  floating `{mv / pos['sl_d']:+.2f}R`"
        opn = (f"{'🟢 LONG' if long else '🔴 SHORT'}  ·  opened {_when(pos['opened'])} UTC\n"
               f"📍 Entry `{_px(sym, pos['entry'])}`\n🛑 SL `{_px(sym, pos['sl'])}`   🎯 TP `{_px(sym, pos['tp'])}`{now_txt}\n"
               f"⏳ Status : *running* (SL / TP abhi hit nahi hua)")
    else:
        opn = "No open trade."
    last = "\n".join(
        f"{'✅' if t['R'] > 0 else '❌'} {t['side']}  ·  {t['why']}  ·  `{t['R']:+.2f}R`  ·  {_when(t['closed'])}"
        for t in mine[-8:][::-1]) or "—"
    return (f"📒 *Track Record*  ·  `{name}`\n"
            f"_(every signal sent to you is also traded on a virtual 1% risk account)_\n\n"
            f"{body}\n\n{all_line}\n\n"
            f"💼 *Paper equity* :  `{p['equity']:,.2f} USDT`  ({ret:+.2f}% since start)\n\n"
            f"📂 *Open trade*\n{opn}\n\n"
            f"🕘 *Last trades*\n{last}")


def _storage_line(sym):
    mongo = "MongoDB ✅" if _cr._mdb_cache is not None else "local file only ⚠️ (Mongo not connected)"
    dq = _cr._lh_hist.get(sym)
    hrs = (dq[-1][0] - dq[0][0]) / 3600 if dq and len(dq) > 1 else 0.0
    return f"💾 *Storage* :  {mongo}\n🔥 *Heatmap data* :  `{hrs:.1f}h` collected (saved to Mongo)"


async def _reply_many(u: Update, c: ContextTypes.DEFAULT_TYPE, parts):
    """Owner-only output, one Telegram message per part. In groups everything goes to the owner's DM."""
    chat = u.effective_chat
    if chat is not None and chat.type == "private":
        for t in parts:
            await _send(c.bot, chat.id, t)
    else:
        ok = True
        for t in parts:
            ok = await _to_owner(c.bot, t) and ok
        await u.message.reply_text("📩 DM check karo." if ok else "⚠️ Pehle bot ko DM me /start karo.")


async def analytics_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/analytics btc | xau   (owner only) — signal now + track record + backtest, everything in one place"""
    if not u.message or not _is_owner(u):
        return
    try:
        sym = _sym_from((u.message.text or "").split()[1:])
        if not sym:
            await _reply_private(u, c, "Supported: `btc`, `xau` (gold)")
            return
        a = await asyncio.get_running_loop().run_in_executor(None, lambda: _analyze_sync(sym))
        parts = [_analysis_text(sym, a) if a else "❌ Binance data abhi nahi mila, thodi der baad try karo."]
        parts.append(_record_text(sym, a["price"] if a else None))
        parts.append(_backtest_text(sym, _state["cfg"].get(sym), _state["hist"].get(sym, []), _storage_line(sym)))
        await _reply_many(u, c, parts)
    except Exception as e:
        _log_exc("[analytics]", e)


# ----------------------------------------------------------------------------- paper trading
def _paper_open(sym, sig, price, bar_ts, cfg):
    p = _paper()
    long = sig["side"] == "LONG"
    entry = price * (1 + SLIP if long else 1 - SLIP)
    sl, tp = _levels(sym, sig, entry)
    risk_usd = p["equity"] * min(RISK, LEV_CAP * sig["sl_d"] / entry)
    p["open"][sym] = {"side": sig["side"], "entry": entry, "sl": sl, "tp": tp, "sl_d": sig["sl_d"],
                      "tp_d": sig["tp_d"], "risk_usd": risk_usd, "opened": time.time(), "bar_ts": bar_ts,
                      "strategy": cfg_name(cfg)}


def _paper_close(sym, exit_px, why):
    p = _paper()
    pos = p["open"].pop(sym)
    long = pos["side"] == "LONG"
    gross = (exit_px - pos["entry"]) if long else (pos["entry"] - exit_px)
    net = gross - pos["entry"] * (2 * FEE + SLIP)
    r = net / pos["sl_d"]
    pnl = pos["risk_usd"] * r
    p["equity"] += pnl
    rec = {"sym": sym, "side": pos["side"], "entry": pos["entry"], "exit": exit_px, "why": why, "R": r,
           "pnl": pnl, "usd_move": net, "closed": time.time(), "held_min": (time.time() - pos["opened"]) / 60}
    p["closed"].append(rec)
    p["closed"] = p["closed"][-300:]
    p["cool"][sym] = time.time() + COOLDOWN_BARS * TF_MS / 1000
    return rec


def _paper_check_sync(sym):
    """Walks the 1m candles since entry (also covers downtime); SL is checked before TP inside a candle."""
    pos = _paper()["open"].get(sym)
    if not pos:
        return None
    start_ms = int(pos["opened"] * 1000) // 60000 * 60000
    rows = _fetch_klines_range(sym, "1m", start_ms, int(time.time() * 1000), max_pages=3)
    if not rows:
        return None
    long = pos["side"] == "LONG"
    for r in sorted(rows, key=lambda r: r[0]):
        hit_sl = r[3] <= pos["sl"] if long else r[2] >= pos["sl"]
        hit_tp = r[2] >= pos["tp"] if long else r[3] <= pos["tp"]
        if hit_sl:
            return pos["sl"], "SL"
        if hit_tp:
            return pos["tp"], "TP"
    if time.time() - pos["opened"] > MAX_HOLD_BARS * TF_MS / 1000:
        return rows[-1][4], "TIME"
    return None


def _close_text(rec):
    icon = {"TP": "🎯", "SL": "🛑", "TIME": "⏱"}[rec["why"]]
    label = {"TP": "TAKE PROFIT HIT ✅", "SL": "STOP LOSS HIT ❌", "TIME": "Time stop (8h), closed at market"}[rec["why"]]
    p = _paper()
    mine = [t for t in p["closed"] if t["sym"] == rec["sym"]]
    wins = sum(1 for t in mine if t["R"] > 0)
    return (f"{icon} *{label}*\n`{SYMBOLS[rec['sym']]['name']}`  ·  {'🟢 LONG' if rec['side'] == 'LONG' else '🔴 SHORT'}\n\n"
            f"📍 *Entry* :  `{_px(rec['sym'], rec['entry'])}`\n"
            f"🏁 *Exit* :  `{_px(rec['sym'], rec['exit'])}`\n\n"
            f"📈 *Result* :  `{rec['R']:+.2f}R`  ·  `{rec['pnl']:+,.2f} USDT`\n\n"
            f"📒 *Record* ({SYMBOLS[rec['sym']]['name']}) :  {wins} wins / {len(mine) - wins} losses  ·  "
            f"win rate {wins / len(mine) * 100:.0f}%\n\n"
            f"💼 *Paper equity* :  `{p['equity']:,.2f} USDT`\n\n{BRAND}")


# ----------------------------------------------------------------------------- automatic optimisation / backtest
def _fetch_history_sync(sym, days):
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows = _fetch_klines_range(sym, TF, start, end, max_pages=int(days * 288 / 1000) + 6)
    return [r[:6] for r in rows if r[0] + TF_MS <= end]


async def run_optimizer(sym, days=HISTORY_DAYS):
    """Downloads history + walk-forward optimisation (thread), stores the result. Returns the result dict."""
    global _opt_lock
    if _opt_lock is None:
        _opt_lock = asyncio.Lock()
    async with _opt_lock:
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, lambda: _fetch_history_sync(sym, days))
        if len(rows) < WARMUP + 1500:
            return {"error": f"Not enough data from Binance ({len(rows)} candles)."}
        res = await loop.run_in_executor(None, lambda: optimize(rows, sym))
        res["days"] = days
        res.setdefault("ts", time.time())
        await _record_backtest(sym, res)
        return res


def _m_line(m):
    return (f"{m['n']} trades  ·  win rate {m['wr']:.0f}%  ·  PF {m['pf']:.2f}  ·  avg {m['exp']:+.2f}R  ·  "
            f"return {m['ret']:+.1f}%  ·  max DD {m['dd']:.1f}%  ·  {m['tpd']:.1f}/day")


def _backtest_text(sym, res, hist=None, extra=""):
    name = SYMBOLS[sym]["name"]
    tail = (f"{extra}\n\n" if extra else "") + BRAND
    if not res:
        return (f"🧪 *Auto Backtest*  ·  `{name}`\n\n⏳ Backtest background me chal raha hai, "
                f"kuch minute me yahan result aa jayega.\n\n{tail}")
    if not res.get("cfg"):
        return (f"🧪 *Auto Backtest*  ·  `{name}`\n\n❌ {res['reason']}\n\n"
                f"{res['tested']} configurations, {res.get('days', HISTORY_DAYS)} din ka 5m data. "
                f"Is market me auto-signals band hain.\n\n{tail}")
    verdict = ("✅ *VALIDATED* on unseen data. Auto-signals ON." if res["validated"]
               else f"❌ *NOT validated*. {res['reason']}\nAuto-signals OFF.")
    hist_txt = ""
    if hist and len(hist) > 1:
        hist_txt = "\n\n🗂 *Previous runs*\n" + "\n".join(
            f"{_when(h['ts'])}  ·  {'✅' if h.get('validated') else '❌'}  ·  "
            + (f"win rate {h['test']['wr']:.0f}%  ·  PF {h['test']['pf']:.2f}  ·  return {h['test']['ret']:+.1f}%"
               if h.get("test") else "no config") for h in hist[1:4])
    return (f"🧪 *Auto Backtest*  ·  `{name}`\n"
            f"_(re-runs every 6h on the last {res.get('days', HISTORY_DAYS)} days, last run {_when(res.get('ts', time.time()))} UTC)_\n\n"
            f"📊 *Best strategy*\n{cfg_name(res['cfg'])}\n\n"
            f"🏋️ *Train* (first {int(TRAIN_FRAC * 100)}%)\n{_m_line(res['train'])}\n\n"
            f"🔬 *Test* (unseen {int((1 - TRAIN_FRAC) * 100)}%)\n{_m_line(res['test'])}\n\n"
            f"{verdict}{hist_txt}\n\n"
            f"ℹ️ {res['tested']} configs tested, fees {FEE * 200:.2f}% + slippage {SLIP * 200:.2f}% per round trip, "
            f"SL first when both hit in one candle. Past results do not guarantee future results.\n\n{tail}")


# ----------------------------------------------------------------------------- background engine
async def _scan_symbol(bot, sym):
    cfg, validated, st = _active_cfg(sym)
    if not validated and not SEND_UNVALIDATED:
        return
    p = _paper()
    expected = (int(time.time() * 1000) // TF_MS) * TF_MS - TF_MS      # open time of the latest closed 5m candle
    if p["last_bar"].get(sym) == expected:
        return
    loop = asyncio.get_running_loop()
    closed, price = await loop.run_in_executor(None, lambda: _closed_candles(sym))
    if not closed or closed[-1][0] < expected:
        return                                                          # candle not published yet, retry next loop
    p["last_bar"][sym] = closed[-1][0]
    if sym in p["open"] or time.time() < p["cool"].get(sym, 0) or len(closed) < WARMUP + 5:
        return
    ind = Ind(closed)
    sig = signal_at(ind, len(closed) - 1, cfg, SYMBOLS[sym]["min_tp"])
    if not sig:
        return
    _paper_open(sym, sig, price, closed[-1][0], cfg)
    _save_state()
    await _to_owner(bot, _signal_text(sym, sig, price, st, validated, opened_paper=True))
    logger.info(f"[trades] signal {sym} {sig['side']} @ {price}")


async def _monitor_paper(bot):
    for sym in list(_paper()["open"]):
        loop = asyncio.get_running_loop()
        hit = await loop.run_in_executor(None, lambda s=sym: _paper_check_sync(s))
        if hit:
            rec = _paper_close(sym, hit[0], hit[1])
            _save_state()
            await _db_log_trade(rec)
            await _to_owner(bot, _close_text(rec))


async def _scanner_loop(bot):
    await asyncio.sleep(45)
    while True:
        try:
            for sym in SYMBOLS:
                await _scan_symbol(bot, sym)
            await _monitor_paper(bot)
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            _log_exc("[trades scanner]", e)
        await asyncio.sleep(SCAN_EVERY)


async def _optimizer_loop(bot):
    """Backtest + walk-forward optimisation runs by itself: at start and then every OPTIMIZE_EVERY seconds."""
    await asyncio.sleep(60)
    while True:
        for sym in SYMBOLS:
            prev = _state["cfg"].get(sym)
            if prev and prev.get("ts") and time.time() - prev["ts"] < OPTIMIZE_EVERY * 0.95:
                continue
            was_valid = prev.get("validated") if prev else None
            try:
                res = await run_optimizer(sym)
                if res.get("error"):
                    logger.warning(f"[trades] optimizer {sym}: {res['error']}")
                else:
                    logger.info(f"[trades] optimizer {sym}: validated={res.get('validated')} tested={res.get('tested')}")
                    if was_valid is None or bool(was_valid) != bool(res.get("validated")):
                        await _to_owner(bot, "🔄 *Auto backtest update*\n\n" +
                                        _backtest_text(sym, res, _state["hist"].get(sym, [])))
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                _log_exc(f"[trades optimizer {sym}]", e)
            await asyncio.sleep(5)
        await asyncio.sleep(1800)


async def _engine_main(bot):
    if not await _db_load():
        _file_load()
    await asyncio.gather(_scanner_loop(bot), _optimizer_loop(bot))


def start_trades_engine(bot):
    """Call once inside the running event loop. Returns the task (cancel it on shutdown)."""
    global _engine_task
    if _engine_task is None or _engine_task.done():
        _engine_task = asyncio.create_task(_engine_main(bot))
        logger.info("[trades] engine started (scanner + daily optimiser)")
    return _engine_task
