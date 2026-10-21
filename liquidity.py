"""
/vf  — Volume Footprint (last 200 candles, buy/sell per price level, delta + cumulative delta)
/lh  — Liquidity Heatmap (Binance order-book depth over time, Coinglass style, candles on top)

Needs: pillow, requests.  Uses helpers from handlers/crypto.py.
Wiring (bot.py): CommandHandler("vf", crypto_vf_handler), CommandHandler("lh", crypto_lh_handler),
                 start_lh_collector() once at startup.
"""
import asyncio
import bisect
import math
import time
from array import array
from collections import deque
from datetime import datetime, timezone

import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import logger
from state import bot_status
from utils import safe_react
from handlers.crypto import (
    BRAND, _BINANCE_HOSTS, _HTTP_UA,
    _BG, _BLUE, _DOWN, _GOLD, _GRID, _TXT, _UP,
    _clean_ticker, _fetch_ohlcv_sync, _fmt_price, _fmt_vol, _font, _kline_sources,
    _log_exc, _now_utc, _png_file, _tf_of, _text,
)

_MUTED = (140, 140, 150)
_WHITE = (255, 255, 255)


def _fmt_usd(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.2f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.1f}K"
    return f"${v:,.0f}"


# =====================================================================
# /vf — VOLUME FOOTPRINT (last 200 candles)
# Per-candle buy/sell volume at each price level, built from lower-timeframe
# candles' taker buy/sell volume (Binance klines), so it is an approximation of
# tick-level footprint but stays fast and light.
# =====================================================================
_VF_CANDLES = 200
_VF_BINS = 100
_VF_SUB = {"5m": "1m", "15m": "1m", "1h": "5m", "4h": "15m", "1d": "1h"}
_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
_VF_TTL = 90

_vf_cache = {}
_vf_locks = {}


def _fetch_klines_range(ticker: str, interval: str, start_ms: int, end_ms: int, max_pages: int = 12):
    """Pages through Binance klines from start_ms to end_ms. Rows: [ts,o,h,l,c,v,taker_buy]."""
    sources = _kline_sources(ticker)
    chosen = None
    rows = []
    cursor = start_ms
    for _ in range(max_pages):
        page = None
        for src in ([chosen] if chosen else []) + [s for s in sources if s != chosen]:
            url, symbol, _label = src
            try:
                r = requests.get(url, params={"symbol": symbol, "interval": interval,
                                              "startTime": cursor, "limit": 1000},
                                 headers=_HTTP_UA, timeout=15)
                if r.status_code == 200:
                    page = r.json()
                    chosen = src
                    break
            except Exception:
                continue
        if not page:
            break
        for k in page:
            rows.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
                         float(k[9]) if len(k) > 9 else None])
        last = int(page[-1][0])
        if last >= end_ms or len(page) < 1000:
            break
        cursor = last + 1
    return rows


def _compute_vf(ticker: str, tf: str):
    parents, _src = _fetch_ohlcv_sync(ticker, tf, _VF_CANDLES)
    if not parents or len(parents) < 30:
        return None
    parents = sorted(parents, key=lambda r: r[0])
    n = len(parents)
    pms = _TF_MS[tf]
    subs = _fetch_klines_range(ticker, _VF_SUB[tf], parents[0][0], parents[-1][0] + pms)
    if len(subs) < n:
        return None

    lo = min(p[3] for p in parents)
    hi = max(p[2] for p in parents)
    if hi <= lo:
        hi = lo * 1.0001 + 1e-12
    pad = (hi - lo) * 0.02
    lo, hi = lo - pad, hi + pad
    bins = _VF_BINS
    step = (hi - lo) / bins

    buy = [[0.0] * bins for _ in range(n)]
    sell = [[0.0] * bins for _ in range(n)]
    ts_list = [p[0] for p in parents]

    for ts, o, h, l, c, v, tb in subs:
        pi = bisect.bisect_right(ts_list, ts) - 1
        if pi < 0 or ts >= ts_list[pi] + pms:
            continue
        t = tb if tb is not None else (v if c >= o else 0.0)
        t = min(max(t, 0.0), v)
        sv = v - t
        i0 = min(bins - 1, max(0, int((l - lo) / step)))
        i1 = min(bins - 1, max(0, int((h - lo) / step)))
        k = i1 - i0 + 1
        bb, ss = buy[pi], sell[pi]
        for j in range(i0, i1 + 1):
            bb[j] += t / k
            ss[j] += sv / k

    delta = [sum(buy[i]) - sum(sell[i]) for i in range(n)]
    cvd, run = [], 0.0
    for dl in delta:
        run += dl
        cvd.append(run)
    return {"parents": parents, "buy": buy, "sell": sell, "lo": lo, "hi": hi, "step": step, "bins": bins,
            "delta": delta, "cvd": cvd,
            "tot_buy": sum(sum(r) for r in buy), "tot_sell": sum(sum(r) for r in sell)}


def _cell_color(b: float, s: float, ref: float):
    t = b + s
    if t <= 0:
        return None
    d = (b - s) / t
    strength = 0.28 + 0.72 * min(1.0, (t / ref) ** 0.5)
    base = _UP if d >= 0 else _DOWN
    k = min(1.0, abs(d) * 1.7)
    neutral = (120, 120, 130)
    col = [neutral[i] + (base[i] - neutral[i]) * k for i in range(3)]
    return tuple(int(_BG[i] + (col[i] - _BG[i]) * strength) for i in range(3))


def _render_vf_sync(vf, ticker: str, tf: str) -> bytes:
    from PIL import Image, ImageDraw

    parents, buy, sell = vf["parents"], vf["buy"], vf["sell"]
    n, bins, lo, hi, step = len(parents), vf["bins"], vf["lo"], vf["hi"], vf["step"]

    W, H = 1700, 1060
    L, R = 30, 215
    TOP, P_BOT = 140, 650
    D_TOP, D_BOT = 685, 800
    C_TOP, C_BOT = 835, 935
    plot_r = W - R
    cw = (plot_r - L) / n

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_title, f_sub = _font(36, True), _font(24, True)
    f_lbl, f_tag = _font(17), _font(18, True)

    def py(p):
        return TOP + (hi - p) / (hi - lo) * (P_BOT - TOP)

    last = parents[-1][4]
    ly = py(last)
    lc = _UP if parents[-1][4] >= parents[-1][1] else _DOWN

    for i in range(7):
        p = lo + (hi - lo) * i / 6
        y = py(p)
        d.line([(L, y), (plot_r, y)], fill=_GRID, width=1)
        if abs(y - ly) > 32:
            _text(d, (plot_r + 14, y), _fmt_price(p), f_lbl, _TXT, "lm")

    totals = sorted(buy[i][j] + sell[i][j] for i in range(n) for j in range(bins) if buy[i][j] + sell[i][j] > 0)
    ref = totals[int(len(totals) * 0.95)] if totals else 1.0
    ref = ref or 1.0

    for i in range(n):
        x0, x1 = L + i * cw + 1, L + (i + 1) * cw - 1
        best_j, best_t = -1, 0.0
        for j in range(bins):
            b, s = buy[i][j], sell[i][j]
            col = _cell_color(b, s, ref)
            if col is None:
                continue
            y0, y1 = py(lo + (j + 1) * step), py(lo + j * step)
            d.rectangle([x0, y0, x1, max(y0 + 1, y1 - 1)], fill=col)
            if b + s > best_t:
                best_t, best_j = b + s, j
        if best_j >= 0:
            y0, y1 = py(lo + (best_j + 1) * step), py(lo + best_j * step)
            d.rectangle([x0, y0, x1, max(y0 + 1, y1 - 1)], outline=_GOLD, width=1)

    # delta bars
    dmax = max(abs(x) for x in vf["delta"]) or 1.0
    zero = (D_TOP + D_BOT) / 2
    d.line([(L, zero), (plot_r, zero)], fill=_GRID, width=1)
    for i, dl in enumerate(vf["delta"]):
        h = abs(dl) / dmax * (D_BOT - D_TOP) / 2
        x0, x1 = L + i * cw + 1, L + (i + 1) * cw - 1
        if dl >= 0:
            d.rectangle([x0, zero - h, x1, zero], fill=_UP)
        else:
            d.rectangle([x0, zero, x1, zero + h], fill=_DOWN)
    _text(d, (L + 6, D_TOP + 6), "Delta", f_lbl, _TXT, "la")

    # cumulative delta line
    cmin, cmax = min(vf["cvd"]), max(vf["cvd"])
    cspan = (cmax - cmin) or 1.0
    pts = [(L + (i + 0.5) * cw, C_BOT - (v - cmin) / cspan * (C_BOT - C_TOP)) for i, v in enumerate(vf["cvd"])]
    d.line([(L, C_BOT), (plot_r, C_BOT)], fill=_GRID, width=1)
    d.line(pts, fill=_BLUE, width=3)
    _text(d, (L + 6, C_TOP + 6), "Cumulative Delta", f_lbl, _TXT, "la")
    cv = vf["cvd"][-1]
    _text(d, (plot_r + 14, pts[-1][1]), _fmt_vol(cv), f_tag, _UP if cv >= 0 else _DOWN, "lm")

    # time labels
    fmt = "%d %b" if tf == "1d" else "%d %b  %H:%M"
    stp = max(1, n // 7)
    for i in range(0, n, stp):
        x = L + (i + 0.5) * cw
        lbl = datetime.fromtimestamp(parents[i][0] / 1000, tz=timezone.utc).strftime(fmt)
        _text(d, (min(max(x, L + 55), plot_r - 55), C_BOT + 22), lbl, f_lbl, _TXT, "ma")

    # last price
    d.line([(L, ly), (plot_r, ly)], fill=lc, width=1)
    tag = _fmt_price(last)
    d.rectangle([plot_r + 4, ly - 16, plot_r + 4 + d.textlength(tag, font=f_tag) + 24, ly + 16], fill=lc)
    _text(d, (plot_r + 16, ly), tag, f_tag, (0, 0, 0), "lm")

    # header + legend
    title = f"{ticker}/USDT"
    _text(d, (L, 72), title, f_title, _WHITE, "ls")
    _text(d, (L + d.textlength(title, font=f_title) + 20, 72), f"Volume Footprint  ·  {tf}", f_sub, (150, 150, 165), "ls")
    lx = L
    for col, label in ((_UP, "Buy dominant"), (_DOWN, "Sell dominant"), (_GOLD, "POC of candle")):
        d.rectangle([lx, 104, lx + 18, 122], fill=col)
        _text(d, (lx + 30, 113), label, f_lbl, _TXT, "lm")
        lx += 30 + d.textlength(label, font=f_lbl) + 46
    _text(d, (L, H - 52), "powered by - BELUGA.PY", f_lbl, _MUTED, "la")

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _build_vf_sync(ticker: str, tf: str):
    vf = _compute_vf(ticker, tf)
    if not vf:
        return None
    return {"png": _render_vf_sync(vf, ticker, tf), "ts": time.time(), "ticker": ticker, "tf": tf,
            "buy": vf["tot_buy"], "sell": vf["tot_sell"]}


async def _get_vf(ticker: str, tf: str):
    key = (ticker, tf)
    e = _vf_cache.get(key)
    if e and time.time() - e["ts"] < _VF_TTL:
        return e
    async with _vf_locks.setdefault(key, asyncio.Lock()):
        e = _vf_cache.get(key)
        if e and time.time() - e["ts"] < _VF_TTL:
            return e
        loop = asyncio.get_running_loop()
        new = await loop.run_in_executor(None, lambda: _build_vf_sync(ticker, tf))
        if new:
            _vf_cache[key] = new
            if len(_vf_cache) > 30:
                for k in sorted(_vf_cache, key=lambda k: _vf_cache[k]["ts"])[:10]:
                    _vf_cache.pop(k, None)
            return new
        return e


def _vf_caption(e) -> str:
    delta = e["buy"] - e["sell"]
    upd = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%H:%M")
    return (f"🗺 *Volume Footprint*\n"
            f"`{e['ticker']}/USDT`  ·  `{e['tf']}`\n\n"
            f"🟢 *Buy Volume* :  `{_fmt_vol(e['buy'])}`\n\n"
            f"🔴 *Sell Volume* :  `{_fmt_vol(e['sell'])}`\n\n"
            f"⚖️ *Delta* :  `{'+' if delta >= 0 else '-'}{_fmt_vol(abs(delta))}`\n\n"
            f"🕒 *Updated* :  `{upd} UTC`\n\n"
            f"{BRAND}")


async def crypto_vf_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/vf <coin> <tf>  e.g. /vf btc 1h   (tf: 5m, 15m, 1h, 4h, 1d)"""
    if not u.message:
        return
    sm = None
    try:
        tokens = (u.message.text or "").split()[1:]
        if len(tokens) == 1 and _tf_of(tokens[0]):
            ticker, tf = "BTC", _tf_of(tokens[0])
        else:
            ticker = _clean_ticker(tokens[0]) if tokens else "BTC"
            tf = next((_tf_of(t) for t in tokens[1:] if _tf_of(t)), "1h")
        if tf not in _VF_SUB:
            await u.message.reply_text("⚠️ Footprint timeframes:\n\n`5m`  `15m`  `1h`  `4h`  `1d`",
                                       parse_mode=ParseMode.MARKDOWN)
            return
        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "⚡")

        e = _vf_cache.get((ticker, tf))
        if not (e and time.time() - e["ts"] < _VF_TTL):
            sm = await u.message.reply_text(
                f"⏳ Building footprint for *{ticker}/USDT* (`{tf}`)...", parse_mode=ParseMode.MARKDOWN)
        e = await asyncio.wait_for(_get_vf(ticker, tf), timeout=90)
        if not e:
            raise ValueError(f"No data for {ticker}/USDT")
        await u.message.reply_photo(photo=_png_file(e["png"], f"VF_{ticker}_{tf}.png"),
                                    caption=_vf_caption(e), parse_mode=ParseMode.MARKDOWN)
        if sm:
            try:
                await sm.delete()
            except Exception:
                pass
    except BaseException as e:
        _log_exc("[crypto_vf]", e)
        bot_status["error_count"] += 1
        if sm:
            try:
                await sm.edit_text(f"❌ Footprint error: {type(e).__name__}: {str(e)[:150]}")
            except Exception:
                pass
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise


# =====================================================================
# /lh — LIQUIDITY HEATMAP (order-book depth collected over time)
# A background task snapshots the Binance order book every ~30s and keeps 12h.
# The heatmap = those snapshots stacked in time, with candles drawn on top.
# History lives in memory, so after a restart it fills up again from zero.
# =====================================================================
_LH_CORE = ["BTC", "ETH", "SOL", "XAU"]
_LH_INTERVAL = 30
_LH_KEEP_SEC = 12 * 3600
_LH_EXTRA_MAX = 4
_LH_EXTRA_IDLE = 6 * 3600
_LH_WINDOWS = {"1h": 1, "2h": 2, "4h": 4, "6h": 6, "12h": 12}
_LH_TTL = 45

_lh_widths = {}      # ticker -> price bucket width
_lh_hist = {}        # ticker -> deque of (ts, mid, array('i') bucket idx, array('f') usd notional)
_lh_extra = {}       # ticker -> last requested ts (non-core coins being tracked)
_lh_cache = {}
_lh_task = None

_CMAP = [(0.0, (10, 8, 32)), (0.18, (45, 20, 115)), (0.40, (25, 95, 170)),
         (0.62, (20, 185, 170)), (0.82, (150, 225, 70)), (1.0, (255, 235, 60))]


def _cmap(x: float):
    x = 0.0 if x < 0 else 1.0 if x > 1 else x
    for (a, ca), (b, cb) in zip(_CMAP, _CMAP[1:]):
        if x <= b:
            t = (x - a) / (b - a) if b > a else 0.0
            return tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
    return _CMAP[-1][1]


_LUT = [_cmap(i / 255) for i in range(256)]


def _nice_width(price: float) -> float:
    raw = price * 0.0001
    exp = math.floor(math.log10(raw))
    base = raw / (10 ** exp)
    m = 1 if base < 1.5 else 2 if base < 3.5 else 5 if base < 7.5 else 10
    return m * (10 ** exp)


def _depth_sources(ticker: str, core: bool):
    lim = 5000 if core else 1000
    if ticker == "XAU":
        return ([("https://fapi.binance.com/fapi/v1/depth", "XAUUSDT", 1000)] +
                [(f"{h}/api/v3/depth", "PAXGUSDT", lim) for h in _BINANCE_HOSTS])
    return [(f"{h}/api/v3/depth", f"{ticker}USDT", lim) for h in _BINANCE_HOSTS]


def _snapshot_sync(ticker: str, core: bool):
    for url, symbol, limit in _depth_sources(ticker, core):
        for lim in ([limit, 1000] if limit > 1000 else [limit]):
            try:
                r = requests.get(url, params={"symbol": symbol, "limit": lim}, headers=_HTTP_UA, timeout=12)
                if r.status_code != 200:
                    continue
                data = r.json()
                bids, asks = data.get("bids"), data.get("asks")
                if not bids or not asks:
                    continue
                mid = (float(bids[0][0]) + float(asks[0][0])) / 2
                w = _lh_widths.setdefault(ticker, _nice_width(mid))
                acc = {}
                for p, q in bids + asks:
                    pf = float(p)
                    k = int(pf // w)
                    acc[k] = acc.get(k, 0.0) + pf * float(q)
                ks = sorted(acc)
                return (time.time(), mid, array("i", ks), array("f", [acc[k] for k in ks]))
            except Exception:
                continue
    return None


def _store_snapshot(ticker: str, snap):
    dq = _lh_hist.setdefault(ticker, deque())
    dq.append(snap)
    cutoff = snap[0] - _LH_KEEP_SEC
    while dq and dq[0][0] < cutoff:
        dq.popleft()


async def lh_collector_loop():
    """Keeps order-book snapshots flowing for BTC/ETH/SOL/XAU (+ any coin users asked for)."""
    await asyncio.sleep(15)
    loop = asyncio.get_running_loop()
    while True:
        t0 = time.time()
        for coin in list(_LH_CORE) + list(_lh_extra):
            core = coin in _LH_CORE
            try:
                snap = await loop.run_in_executor(None, lambda c=coin, k=core: _snapshot_sync(c, k))
                if snap:
                    _store_snapshot(coin, snap)
            except Exception as e:
                logger.warning(f"[lh_collector] {coin}: {str(e)[:80]}")
            await asyncio.sleep(1)
        for coin in [k for k, ts in _lh_extra.items() if time.time() - ts > _LH_EXTRA_IDLE]:
            _lh_extra.pop(coin, None)
            _lh_hist.pop(coin, None)
        await asyncio.sleep(max(5.0, _LH_INTERVAL - (time.time() - t0)))


def start_lh_collector():
    """Call once inside the running event loop. Returns the task (cancel it on shutdown)."""
    global _lh_task
    if _lh_task is None or _lh_task.done():
        _lh_task = asyncio.create_task(lh_collector_loop())
        logger.info("[lh_collector] background task started")
    return _lh_task


async def _track_extra(ticker: str) -> bool:
    """Start tracking a non-core coin. Returns False if Binance has no order book for it."""
    loop = asyncio.get_running_loop()
    snap = await loop.run_in_executor(None, lambda: _snapshot_sync(ticker, False))
    if not snap:
        return False
    if len(_lh_extra) >= _LH_EXTRA_MAX:
        oldest = min(_lh_extra, key=lambda k: _lh_extra[k])
        _lh_extra.pop(oldest, None)
        _lh_hist.pop(oldest, None)
    _lh_extra[ticker] = time.time()
    _store_snapshot(ticker, snap)
    return True


def _walls(snap, w: float):
    _ts, mid, ks, vs = snap
    bid, ask = (0.0, 0.0), (0.0, 0.0)
    for k, v in zip(ks, vs):
        p = (k + 0.5) * w
        if p < mid:
            if v > bid[1]:
                bid = (p, v)
        elif v > ask[1]:
            ask = (p, v)
    return bid, ask


def _pick_interval(span_sec: float):
    want = span_sec / 60 / 90
    for mins, name in ((1, "1m"), (3, "3m"), (5, "5m"), (15, "15m"), (30, "30m"), (60, "1h")):
        if mins >= want:
            return mins * 60, name
    return 3600, "1h"


def _render_lh_sync(ticker: str, window_h: int, snaps, candles, interval_sec: int, w: float) -> bytes:
    from PIL import Image, ImageDraw
    import io

    t1 = snaps[-1][0]
    t0 = max(snaps[0][0], t1 - window_h * 3600)
    if t1 - t0 < 300:
        t0 = t1 - 300
    span = t1 - t0
    use = [s for s in snaps if s[0] >= t0]
    mid_last = use[-1][1]

    cand = [c for c in candles if t0 - interval_sec <= c[0] / 1000 <= t1 + interval_sec]
    ylo = min([c[3] for c in cand] + [mid_last])
    yhi = max([c[2] for c in cand] + [mid_last])
    pad = (yhi - ylo) * 0.15 + mid_last * 0.001
    ylo, yhi = ylo - pad, yhi + pad

    idx_lo, idx_hi = int(ylo // w), int(yhi // w)
    nb = idx_hi - idx_lo + 1
    f = max(1, math.ceil(nb / 400))
    nrows = math.ceil(nb / f)
    lo_ax, hi_ax = idx_lo * w, (idx_lo + nrows * f) * w

    ncols = int(min(480, max(12, span / _LH_INTERVAL)))
    cols = [None] * ncols
    vals = []
    for ts, _mid, ks, vs in use:
        c = min(ncols - 1, int((ts - t0) / span * ncols))
        agg = {}
        for k, v in zip(ks, vs):
            r = (k - idx_lo) // f
            if 0 <= r < nrows:
                agg[r] = agg.get(r, 0.0) + v
        cur = cols[c]
        if cur is None:
            cols[c] = agg
        else:
            for r, v in agg.items():
                if v > cur.get(r, 0.0):
                    cur[r] = v
        vals.extend(agg.values())
    last = None
    for c in range(ncols):
        if cols[c] is None:
            cols[c] = last
        else:
            last = cols[c]

    vals.sort()
    ref = vals[int(len(vals) * 0.97)] if vals else 1.0
    ref = ref or 1.0
    k_log = math.log1p(25.0)

    bg = _LUT[0]
    pix = []
    for r in range(nrows - 1, -1, -1):
        for c in range(ncols):
            col = cols[c]
            v = col.get(r, 0.0) if col else 0.0
            pix.append(bg if v <= 0 else _LUT[min(255, int(math.log1p(v / ref * 25.0) / k_log * 255))])

    W, H = 1500, 900
    L, plot_r = 30, 1150
    TOP, BOT = 140, 745
    plot_w, plot_h = plot_r - L, BOT - TOP

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_title, f_sub = _font(36, True), _font(24, True)
    f_lbl, f_tag = _font(17), _font(18, True)

    hm = Image.new("RGB", (ncols, nrows))
    hm.putdata(pix)
    img.paste(hm.resize((plot_w, plot_h), Image.Resampling.BILINEAR), (L, TOP))

    def py(p):
        return TOP + (hi_ax - p) / (hi_ax - lo_ax) * plot_h

    def px(ts_sec):
        return L + (ts_sec - t0) / span * plot_w

    # candles on top of the heatmap
    cwpx = max(2.0, interval_sec / span * plot_w * 0.62)
    for ts, o, h, l, c, _v, *_r in cand:
        x = px(ts / 1000 + interval_sec / 2)
        if x < L - cwpx or x > plot_r + cwpx:
            continue
        col = (0, 235, 150) if c >= o else (255, 70, 110)
        d.line([(x, py(h)), (x, py(l))], fill=(235, 235, 235), width=1)
        y1, y2 = py(max(o, c)), py(min(o, c))
        if y2 - y1 < 1:
            y2 = y1 + 1
        d.rectangle([x - cwpx / 2, y1, x + cwpx / 2, y2], fill=col, outline=(0, 0, 0))

    # axes: price labels, time labels, last price
    ly = py(mid_last)
    for i in range(7):
        p = lo_ax + (hi_ax - lo_ax) * i / 6
        y = py(p)
        d.line([(plot_r, y), (plot_r + 8, y)], fill=_TXT, width=1)
        if abs(y - ly) > 32:
            _text(d, (plot_r + 16, y), _fmt_price(p), f_lbl, _TXT, "lm")
    tfmt = "%H:%M" if span <= 20 * 3600 else "%d %b %H:%M"
    for i in range(6):
        ts = t0 + span * i / 5
        lbl = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(tfmt)
        _text(d, (min(max(px(ts), L + 40), plot_r - 40), BOT + 20), lbl, f_lbl, _TXT, "ma")

    _dashed_line(d, L, plot_r, ly)
    tag = _fmt_price(mid_last)
    d.rectangle([plot_r + 6, ly - 16, plot_r + 6 + d.textlength(tag, font=f_tag) + 24, ly + 16], fill=_WHITE)
    _text(d, (plot_r + 18, ly), tag, f_tag, (0, 0, 0), "lm")

    # colour bar
    bx0, bx1 = 1415, 1440
    for y in range(TOP, BOT):
        x = 1 - (y - TOP) / plot_h
        d.line([(bx0, y), (bx1, y)], fill=_LUT[int(x * 255)], width=1)
    _text(d, ((bx0 + bx1) / 2, TOP - 14), "High", f_lbl, _TXT, "ms")
    _text(d, ((bx0 + bx1) / 2, BOT + 28), "Low", f_lbl, _TXT, "ms")

    title = f"{ticker}/USDT"
    _text(d, (L, 72), title, f_title, _WHITE, "ls")
    _text(d, (L + d.textlength(title, font=f_title) + 20, 72), f"Liquidity Heatmap  ·  {window_h}h", f_sub, (150, 150, 165), "ls")
    _text(d, (L, 112), "Bright = large resting orders", f_lbl, _MUTED, "lm")
    _text(d, (L, H - 52), "powered by - BELUGA.PY", f_lbl, _MUTED, "la")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _dashed_line(d, x1, x2, y, color=(255, 255, 255), dash=10, gap=6):
    x = x1
    while x < x2:
        d.line([(x, y), (min(x + dash, x2), y)], fill=color, width=1)
        x += dash + gap


def _build_lh_sync(ticker: str, window_h: int, snaps):
    w = _lh_widths.get(ticker)
    if not w or len(snaps) < 2:
        return None
    t1 = snaps[-1][0]
    t0 = max(snaps[0][0], t1 - window_h * 3600)
    span = max(300.0, t1 - t0)
    interval_sec, name = _pick_interval(span)
    limit = min(1000, int(span / interval_sec) + 3)
    candles, _src = _fetch_ohlcv_sync(ticker, name, limit)
    png = _render_lh_sync(ticker, window_h, snaps, candles or [], interval_sec, w)
    bid, ask = _walls(snaps[-1], w)
    return {"png": png, "ts": time.time(), "ticker": ticker, "window": window_h,
            "bid": bid, "ask": ask, "span": t1 - snaps[0][0]}


def _lh_caption(e) -> str:
    upd = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%H:%M")
    note = ""
    if e["span"] < 1800:
        note = "⏳ History abhi build ho rahi hai, heatmap time ke saath aur bharega.\n\n"
    return (f"🔥 *Liquidity Heatmap*\n"
            f"`{e['ticker']}/USDT`  ·  `{e['window']}h`\n\n"
            f"🟢 *Biggest Bid Wall* :  `{_fmt_price(e['bid'][0])}`  ·  `{_fmt_usd(e['bid'][1])}`\n\n"
            f"🔴 *Biggest Ask Wall* :  `{_fmt_price(e['ask'][0])}`  ·  `{_fmt_usd(e['ask'][1])}`\n\n"
            f"{note}"
            f"🕒 *Updated* :  `{upd} UTC`\n\n"
            f"{BRAND}")


def _parse_lh(tokens):
    if not tokens:
        return "BTC", 4
    if len(tokens) == 1 and tokens[0].lower() in _LH_WINDOWS:
        return "BTC", _LH_WINDOWS[tokens[0].lower()]
    ticker = _clean_ticker(tokens[0])
    window = next((_LH_WINDOWS[t.lower()] for t in tokens[1:] if t.lower() in _LH_WINDOWS), 4)
    return ticker, window


async def crypto_lh_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/lh <coin> <window>  e.g. /lh btc, /lh eth 12h   (window: 1h 2h 4h 6h 12h, default 4h)"""
    if not u.message:
        return
    try:
        ticker, window = _parse_lh((u.message.text or "").split()[1:])
        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "🔥")

        if ticker not in _LH_CORE and ticker not in _lh_extra:
            if not await _track_extra(ticker):
                await u.message.reply_text(f"❌ Order book data nahi mila: *{ticker}/USDT*",
                                           parse_mode=ParseMode.MARKDOWN)
                return
            await u.message.reply_text(
                f"🔥 *{ticker}/USDT* heatmap tracking shuru ho gayi.\n\n"
                f"Data jama hone me kuch minute lagte hain, thodi der baad `/lh {ticker.lower()}` dubara bhejo.\n\n{BRAND}",
                parse_mode=ParseMode.MARKDOWN)
            return
        if ticker in _lh_extra:
            _lh_extra[ticker] = time.time()

        snaps = list(_lh_hist.get(ticker, ()))
        if len(snaps) < 2:
            await u.message.reply_text(
                f"⏳ *{ticker}/USDT* heatmap data abhi build ho raha hai.\n\nKuch minute baad dubara try karo.\n\n{BRAND}",
                parse_mode=ParseMode.MARKDOWN)
            return

        key = (ticker, window)
        e = _lh_cache.get(key)
        if not (e and time.time() - e["ts"] < _LH_TTL):
            loop = asyncio.get_running_loop()
            e = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: _build_lh_sync(ticker, window, snaps)), timeout=60)
            if not e:
                raise ValueError("heatmap data not ready")
            _lh_cache[key] = e
            if len(_lh_cache) > 30:
                for k in sorted(_lh_cache, key=lambda k: _lh_cache[k]["ts"])[:10]:
                    _lh_cache.pop(k, None)

        await u.message.reply_photo(photo=_png_file(e["png"], f"LH_{ticker}_{window}h.png"),
                                    caption=_lh_caption(e), parse_mode=ParseMode.MARKDOWN)
    except BaseException as e:
        _log_exc("[crypto_lh]", e)
        bot_status["error_count"] += 1
        try:
            await u.message.reply_text(f"❌ Heatmap error: {type(e).__name__}: {str(e)[:150]}")
        except Exception:
            pass
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
