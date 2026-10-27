"""
Crypto commands: /price, /topgainers, /toplosers, /chart (+ text trigger), /vp, /vf, /lh

Wired in bot.py: CommandHandler chart*/vp/vf/lh/price/topgainers/toplosers, text trigger CHART_TEXT_REGEX,
button_callback_handler (chart Refresh), start_vp_warmup() (also starts the /lh order-book collector).
Fonts: put Poppins-Bold.ttf + Poppins-Medium.ttf next to this file (or in a fonts/ folder).
"""
import asyncio
import bisect
import importlib.util
import io
import json
import math
import os
import re
import time
import traceback
import zlib
from array import array
from collections import deque
from datetime import datetime, timezone

import ccxt
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes

import texts as T
from config import logger
from state import bot_status, cache_movers
from utils import safe_react

BRAND = "powered by - BELUGA.PY 🍂"

_HTTP_UA = {"User-Agent": "Mozilla/5.0 (compatible; BelugaBot/12)"}
_BINANCE_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com")


def get_exchange(prefer: str = "binance"):
    """
    Synchronous, blocking network call (ccxt.load_markets()).
    NEVER call this at module import time — it must only run inside the
    event loop via init_exchange_async() so it can't delay HTTP port
    binding or the Telegram polling startup.
    """
    exchanges = ["binance", "bybit", "okx", "bitget", "kraken"]
    if prefer in exchanges:
        exchanges.remove(prefer)
        exchanges.insert(0, prefer)
    for ex_name in exchanges:
        try:
            ex_cls = getattr(ccxt, ex_name)
            ex = ex_cls({'enableRateLimit': True, 'timeout': 12000})
            ex.load_markets()
            logger.info(f"Exchange connected: {ex_name}")
            return ex
        except Exception as e:
            logger.warning(f"{ex_name} failed: {str(e)[:60]}")
    logger.error("No exchange available")
    return None


exchange = None


async def init_exchange_async():
    """Run the blocking exchange connection in a thread so it never blocks the event loop."""
    global exchange
    loop = asyncio.get_running_loop()
    exchange = await loop.run_in_executor(None, get_exchange)


# --- ROBUST PRICE FETCHER USING DIRECT BINANCE PUBLIC REST API ---
def _fetch_price_sync(ticker: str):
    """Fetches ticker data using direct public Binance endpoints with CCXT fallback."""
    symbol = f"{ticker.upper()}USDT"

    for host in _BINANCE_HOSTS:
        try:
            r = requests.get(f"{host}/api/v3/ticker/24hr", params={"symbol": symbol}, headers=_HTTP_UA, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return {
                    "last": float(data.get("lastPrice", 0)),
                    "percentage": float(data.get("priceChangePercent", 0)),
                    "baseVolume": float(data.get("volume", 0)),
                    "high": float(data.get("highPrice", 0)),
                    "low": float(data.get("lowPrice", 0)),
                    "source": "Binance"
                }
        except Exception:
            continue

    # Fallback to connected exchange via CCXT if direct HTTP fails
    if exchange is not None:
        try:
            td = exchange.fetch_ticker(f"{ticker.upper()}/USDT")
            return {
                "last": td.get('last', 0.0),
                "percentage": td.get('percentage', 0.0),
                "baseVolume": td.get('baseVolume', 0.0),
                "high": td.get('high', 0.0),
                "low": td.get('low', 0.0),
                "source": str(getattr(exchange, "name", "Exchange"))
            }
        except Exception:
            pass
    return None


async def crypto_price_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    try:
        ticker = (c.args[0].upper() if c.args else "BTC")
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "⚡")
        sm = await u.message.reply_text(T.crypto_price_msg_1(ticker), parse_mode=ParseMode.MARKDOWN)

        loop = asyncio.get_running_loop()
        try:
            td = await loop.run_in_executor(None, lambda: _fetch_price_sync(ticker))
            if not td or td['last'] <= 0:
                raise ValueError(f"Could not fetch price for {ticker}")

            price = td['last']
            change = td['percentage']
            vol = td['baseVolume']
            high = td['high']
            low = td['low']
            source = td['source']

            sign = "🟩 +" if change >= 0 else "🟥 "
            res = (f"⚡ *{ticker}/USDT*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"🏷 *Price*\n`{_fmt_price(price)} USDT`\n\n"
                   f"📊 *24h Change*\n`{sign}{change:.2f}%`\n\n"
                   f"📈 *24h High*\n`{_fmt_price(high)}`\n\n"
                   f"📉 *24h Low*\n`{_fmt_price(low)}`\n\n"
                   f"🔄 *Volume*\n`{vol:,.2f} {ticker}`\n\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n🐾 _via Beluga Quant Engine · {source}_")
            await sm.edit_text(res, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await sm.edit_text(T.crypto_price_msg_2(e))
            bot_status["error_count"] += 1
    except Exception as e:
        logger.error(f"[crypto_price] {e}")


# Coins that would only pollute a "movers" list (stablecoins / fiat pairs)
_STABLE_BASES = {
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDD", "PYUSD", "USDE", "USD1", "RLUSD",
    "UST", "USTC", "EUR", "EURI", "AEUR", "GBP", "TRY", "BRL", "ARS", "UAH", "PLN", "RON", "CZK", "JPY",
}
_MIN_QUOTE_VOL = 3_000_000


def _fmt_price(p: float) -> str:
    """Readable price at any scale: 64,210.5 / 3.4521 / 0.00001234."""
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    if p >= 0.01:
        return f"{p:.5f}"
    return f"{p:.8f}"


def _fetch_movers_sync():
    errors = []
    # 1) Binance
    for host in _BINANCE_HOSTS:
        try:
            r = requests.get(f"{host}/api/v3/ticker/24hr", headers=_HTTP_UA, timeout=12)
            if r.status_code != 200:
                errors.append(f"{host} HTTP {r.status_code}")
                continue
            recs = []
            for t in r.json():
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                base = sym[:-4]
                if not base or base in _STABLE_BASES:
                    continue
                try:
                    ch, pr, qv = float(t["priceChangePercent"]), float(t["lastPrice"]), float(t["quoteVolume"])
                except (KeyError, TypeError, ValueError):
                    continue
                if pr <= 0 or qv < _MIN_QUOTE_VOL:
                    continue
                recs.append({"sym": base, "ch": ch, "price": pr, "vol": qv})
            if recs:
                return recs, "Binance"
            errors.append(f"{host} returned no usable pairs")
        except Exception as e:
            errors.append(f"{host}: {str(e)[:60]}")

    # 2) CoinGecko
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": 1,
                    "sparkline": "false", "price_change_percentage": "24h"},
            headers=_HTTP_UA, timeout=12,
        )
        if r.status_code == 200:
            recs = []
            for coin in r.json():
                sym = (coin.get("symbol") or "").upper()
                ch, pr, vol = coin.get("price_change_percentage_24h"), coin.get("current_price"), coin.get("total_volume") or 0
                if not sym or sym in _STABLE_BASES or ch is None or not pr or vol < _MIN_QUOTE_VOL:
                    continue
                recs.append({"sym": sym, "ch": float(ch), "price": float(pr), "vol": float(vol)})
            if recs:
                return recs, "CoinGecko"
        else:
            errors.append(f"coingecko HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"coingecko: {str(e)[:60]}")

    # 3) ccxt exchange
    ex = exchange
    if ex is not None:
        try:
            recs = []
            for sym, t in ex.fetch_tickers().items():
                if not sym.endswith("/USDT"):
                    continue
                base = sym.split("/")[0]
                if base in _STABLE_BASES:
                    continue
                pr, ch, qv = t.get("last"), t.get("percentage"), t.get("quoteVolume")
                if ch is None and t.get("open") and pr:
                    ch = (pr / t["open"] - 1) * 100
                if pr is None or ch is None or float(pr) <= 0:
                    continue
                if qv is not None and float(qv) < _MIN_QUOTE_VOL:
                    continue
                recs.append({"sym": base, "ch": float(ch), "price": float(pr), "vol": float(qv or 0)})
            if recs:
                return recs, str(getattr(ex, "name", "Exchange"))
        except Exception as e:
            errors.append(f"ccxt: {str(e)[:60]}")

    logger.warning(f"[crypto_movers] all sources failed: {' | '.join(errors)}")
    return [], ""


async def crypto_movers_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/topgainers and /toplosers: top 5 coins by 24h change (liquid coins only)."""
    if not u.message:
        return
    try:
        first = (u.message.text or "").split()[0].lower() if (u.message.text or "").strip() else ""
        gainers_mode = first.startswith("/topgainers")
        lbl = "Gainers" if gainers_mode else "Losers"
        sm = await u.message.reply_text(T.crypto_movers_msg_1(lbl), parse_mode=ParseMode.MARKDOWN)

        now = time.time()
        if cache_movers.get("data") and cache_movers.get("ts") and (now - cache_movers["ts"]) < 60:
            records, source = cache_movers["data"]
        else:
            loop = asyncio.get_running_loop()
            try:
                records, source = await asyncio.wait_for(loop.run_in_executor(None, _fetch_movers_sync), timeout=45)
            except Exception as e:
                await sm.edit_text(T.crypto_movers_msg_3(e))
                return
            if records:
                cache_movers["ts"] = now
                cache_movers["data"] = (records, source)

        if not records:
            await sm.edit_text(T.CRYPTO_MOVERS_MSG_4)
            return

        records = sorted(records, key=lambda x: x["ch"], reverse=gainers_mode)[:5]
        text = f"📊 *TOP 5 {lbl.upper()} (24H)*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, r in enumerate(records, 1):
            s = "🟩 +" if r["ch"] >= 0 else "🟥 "
            text += (f"*{i}. {r['sym']}*\nPrice: `{_fmt_price(r['price'])}` USDT\n"
                     f"Change: `{s}{r['ch']:.2f}%`\nVolume: `{r['vol'] / 1e6:,.1f}M` USDT\n\n")
        text += f"━━━━━━━━━━━━━━━━━━━━\n🐾 _via Beluga Quant Engine · {source}_"
        await sm.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[crypto_movers] {e}")


# =====================================================================
# SHARED HELPERS (candles from Binance, arg parsing)
# Charts are drawn with Pillow only. Requires: pillow (requirements.txt)
# =====================================================================
_TF_MAPPING = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h",
    "1d": "1d", "1D": "1d", "1m": "1M", "1M": "1M",
}
_COIN_ALIASES = {"GOLD": "XAU", "XAUUSD": "XAU"}

_UP = (0, 196, 140)
_DOWN = (255, 51, 102)
_BG = (13, 13, 13)
_GRID = (34, 34, 34)
_TXT = (204, 204, 204)
_GOLD = (255, 204, 0)
_BLUE = (100, 170, 255)

# Text trigger: "chart btc 1h" / "chart eth" (no slash). Register with filters.Regex(CHART_TEXT_REGEX)
CHART_TEXT_REGEX = r"(?i)^\s*chart(?:@\w+)?\s+[a-z0-9]{2,10}(?:\s+(?:5m|15m|1h|4h|1d|1m))?\s*$"


def _clean_ticker(raw: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if t.endswith("USDT") and len(t) > 4:
        t = t[:-4]
    t = _COIN_ALIASES.get(t, t)
    return t[:15] or "BTC"


def _tf_of(tok: str):
    if tok in _TF_MAPPING:
        return _TF_MAPPING[tok]
    if tok.lower() in _TF_MAPPING:
        return _TF_MAPPING[tok.lower()]
    return None


def _parse_ticker_tf(tokens, default_tf="1h"):
    """tokens = words after the command, e.g. ['btc', '4h'] -> ('BTC', '4h')."""
    if not tokens:
        return "BTC", default_tf
    if len(tokens) == 1 and _tf_of(tokens[0]):
        return "BTC", _tf_of(tokens[0])
    ticker = _clean_ticker(tokens[0])
    tf = default_tf
    for tok in tokens[1:]:
        found = _tf_of(tok)
        if found:
            tf = found
            break
    return ticker, tf


def _kline_sources(ticker: str):
    """(url, symbol, label) list in priority order. XAU = Binance gold futures, PAXG spot as fallback."""
    t = ticker.upper()
    if t == "XAU":
        return ([("https://fapi.binance.com/fapi/v1/klines", "XAUUSDT", "Binance XAUUSDT")] +
                [(f"{h}/api/v3/klines", "PAXGUSDT", "Binance PAXG (gold)") for h in _BINANCE_HOSTS])
    return [(f"{h}/api/v3/klines", f"{t}USDT", "Binance") for h in _BINANCE_HOSTS]


def _fetch_ohlcv_sync(ticker: str, timeframe: str, limit: int = 60):
    """
    Returns (rows, source). rows = [[ts_ms, o, h, l, c, v, taker_buy_vol], ...]
    Binance public REST first (works from Render via data-api.binance.vision), ccxt fallback.
    """
    for url, symbol, label in _kline_sources(ticker):
        try:
            r = requests.get(url, params={"symbol": symbol, "interval": timeframe, "limit": limit},
                             headers=_HTTP_UA, timeout=12)
            if r.status_code == 200:
                data = r.json()
                if data:
                    rows = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
                             float(k[9]) if len(k) > 9 else None] for k in data]
                    return rows, label
        except Exception:
            continue

    if exchange is not None:
        try:
            raw = exchange.fetch_ohlcv(f"{ticker}/USDT", timeframe, limit=limit)
            rows = []
            for k in raw or []:
                if None in k[:6]:
                    continue
                rows.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), None])
            if rows:
                return rows, str(getattr(exchange, "name", "Exchange"))
        except Exception as e:
            logger.warning(f"[ohlcv] ccxt fallback failed: {str(e)[:80]}")
    return None, ""


def _png_file(data: bytes, name: str) -> io.BytesIO:
    bio = io.BytesIO(data)
    bio.name = name
    return bio


def _log_exc(tag: str, e: BaseException):
    tail = " || ".join(traceback.format_exc().splitlines()[-10:])
    logger.error(f"{tag} {type(e).__name__}: {str(e)[:100]} | TB: {tail}")


_FONT_CACHE = {}
_FONT_FILES = {
    "bold": ["Poppins-Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Roboto-Bold.ttf"],
    "reg": ["Poppins-Medium.ttf", "Poppins-Regular.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Roboto-Regular.ttf"],
}


def _font_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    dirs = [os.environ.get("BELUGA_FONT_DIR", ""), here, os.path.join(here, "fonts"), parent,
            os.path.join(parent, "fonts"), "/usr/share/fonts/truetype/dejavu",
            "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"]
    try:
        spec = importlib.util.find_spec("matplotlib")
        if spec and spec.submodule_search_locations:
            dirs.append(os.path.join(list(spec.submodule_search_locations)[0], "mpl-data", "fonts", "ttf"))
    except Exception:
        pass
    return [x for x in dirs if x and os.path.isdir(x)]


def _font(size, bold=False):
    """Poppins (bundled .ttf next to this file) -> DejaVu/Liberation -> Pillow default."""
    key = (size, bold)
    f = _FONT_CACHE.get(key)
    if f:
        return f
    from PIL import ImageFont
    for name in _FONT_FILES["bold" if bold else "reg"]:
        for folder in _font_dirs():
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                try:
                    f = ImageFont.truetype(path, size)
                    break
                except Exception:
                    f = None
        if f:
            break
    if f is None:
        try:
            f = ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _text(d, xy, s, font, fill, anchor="la"):
    try:
        d.text(xy, s, font=font, fill=fill, anchor=anchor)
    except (ValueError, TypeError):  # bitmap fallback font has no anchor support
        d.text(xy, s, font=font, fill=fill)


def _dashed(d, x1, x2, y, color, dash=8, gap=5):
    x = x1
    while x < x2:
        d.line([(x, y), (min(x + dash, x2), y)], fill=color, width=1)
        x += dash + gap


def _fmt_vol(x: float) -> str:
    a = abs(x)
    if a >= 1e9:
        return f"{x / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{x / 1e6:,.2f}M"
    if a >= 1e3:
        return f"{x / 1e3:,.2f}K"
    return f"{x:,.2f}"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


# =====================================================================
# /chart  — candles + volume, Refresh button only
# =====================================================================
def _render_chart_sync(rows, ticker: str, timeframe: str) -> bytes:
    """Candles + volume drawn straight onto a PIL image."""
    from PIL import Image, ImageDraw

    rows = sorted((r[:6] for r in rows), key=lambda r: r[0])
    n = len(rows)

    W, H = 1300, 880
    L, R = 30, 215
    TOP, price_bottom = 125, 565
    vol_top, vol_bottom = 595, 750
    plot_r = W - R
    plot_w = plot_r - L

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_title, f_big, f_sub = _font(36, True), _font(30, True), _font(24, True)
    f_lbl, f_tag = _font(17), _font(18, True)

    max_h = max(r[2] for r in rows)
    min_l = min(r[3] for r in rows)
    span = (max_h - min_l) or (max_h * 0.01) or 1e-9
    hi, lo = max_h + span * 0.05, min_l - span * 0.05
    max_v = max(r[5] for r in rows) or 1.0

    def py(p):
        return TOP + (hi - p) / (hi - lo) * (price_bottom - TOP)

    last = rows[-1][4]
    ly = py(last)
    lc = _UP if rows[-1][4] >= rows[-1][1] else _DOWN
    tag = _fmt_price(last)

    for i in range(6):
        p = lo + (hi - lo) * i / 5
        y = py(p)
        d.line([(L, y), (plot_r, y)], fill=_GRID, width=1)
        if abs(y - ly) > 32:
            _text(d, (plot_r + 14, y), _fmt_price(p), f_lbl, _TXT, "lm")

    cw = plot_w / n
    body_half = max(1.0, cw * 0.35)
    for i, (_, o, h, l, c, v) in enumerate(rows):
        x = L + (i + 0.5) * cw
        col = _UP if c >= o else _DOWN
        d.line([(x, py(h)), (x, py(l))], fill=col, width=1)
        y1, y2 = py(max(o, c)), py(min(o, c))
        if y2 - y1 < 1:
            y2 = y1 + 1
        d.rectangle([x - body_half, y1, x + body_half, y2], fill=col)
        vh = v / max_v * (vol_bottom - vol_top)
        d.rectangle([x - body_half, vol_bottom - vh, x + body_half, vol_bottom], fill=col)

    d.line([(L, vol_bottom), (plot_r, vol_bottom)], fill=_GRID, width=1)
    _text(d, (plot_r + 14, vol_top + 12), "Volume", f_lbl, _TXT, "lm")

    fmt = "%d %b" if timeframe in ("1d", "1M") else "%d %b  %H:%M"
    for k in range(5):
        i = min(n - 1, int((k + 0.5) / 5 * n))
        x = L + (i + 0.5) * cw
        lbl = datetime.fromtimestamp(rows[i][0] / 1000, tz=timezone.utc).strftime(fmt)
        _text(d, (min(max(x, L + 55), plot_r - 55), vol_bottom + 18), lbl, f_lbl, _TXT, "ma")

    d.line([(L, ly), (plot_r, ly)], fill=lc, width=1)
    tw = d.textlength(tag, font=f_tag)
    d.rectangle([plot_r + 4, ly - 16, plot_r + 4 + tw + 24, ly + 16], fill=lc)
    _text(d, (plot_r + 16, ly), tag, f_tag, (0, 0, 0), "lm")

    title = f"{ticker}/USDT"
    _text(d, (L, 70), title, f_title, (255, 255, 255), "ls")
    _text(d, (L + d.textlength(title, font=f_title) + 20, 70), timeframe, f_sub, (150, 150, 165), "ls")
    _text(d, (plot_r, 70), tag, f_big, lc, "rs")
    _text(d, (L, H - 52), "powered by - BELUGA.PY", f_lbl, (140, 140, 150), "la")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_chart(ticker: str, timeframe: str):
    """Returns (png_bytes, last_price). Raises on failure."""
    loop = asyncio.get_running_loop()
    rows, _source = await loop.run_in_executor(None, lambda: _fetch_ohlcv_sync(ticker, timeframe, 60))
    if not rows:
        raise ValueError(f"No candle data for {ticker}/USDT")
    png = await loop.run_in_executor(None, lambda: _render_chart_sync(rows, ticker, timeframe))
    return png, rows[-1][4]


def _chart_keyboard(ticker: str, timeframe: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"chart_{ticker}_{timeframe}")]])


def _chart_caption(ticker: str, timeframe: str, last: float) -> str:
    return (f"⚡ *{ticker}/USDT*  ·  `{timeframe}`\n\n"
            f"💵 *Price* :  `{_fmt_price(last)}`\n\n"
            f"🕒 *Updated* :  `{_now_utc()} UTC`\n\n"
            f"{BRAND}")


async def _send_chart(message, ticker: str, timeframe: str, quiet: bool = False):
    """Used by /chart, plain-text 'chart btc 1h' and nothing else. quiet=True -> no status/error messages."""
    sm = None
    if not quiet:
        sm = await message.reply_text(
            f"⚡ Generating chart for *{ticker}/USDT* (`{timeframe}`)...", parse_mode=ParseMode.MARKDOWN
        )
    try:
        png, last = await _make_chart(ticker, timeframe)
        if sm:
            try:
                await sm.delete()
            except Exception:
                pass
        await message.reply_photo(photo=_png_file(png, f"{ticker}_{timeframe}.png"),
                                  caption=_chart_caption(ticker, timeframe, last),
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=_chart_keyboard(ticker, timeframe))
    except BaseException as e:  # RecursionError included
        _log_exc(f"[crypto_chart] {ticker} {timeframe}:", e)
        bot_status["error_count"] += 1
        if sm:
            try:
                await sm.edit_text(f"❌ Chart error for {ticker}/USDT: {type(e).__name__}: {str(e)[:150]}")
            except Exception:
                pass
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise


async def crypto_chart_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/chart <coin> <tf>  e.g. /chart btc 1h  (tf: 5m, 15m, 1h, 4h, 1d, 1M)"""
    if not u.message:
        return
    try:
        parts = (u.message.text or "").split()
        cmd = parts[0].lstrip("/").split("@")[0].lower() if parts else "chart"
        default_tf = _tf_of(cmd[5:]) if len(cmd) > 5 else None   # /chart5m, /chart1h, /chart4h ...
        ticker, timeframe = _parse_ticker_tf(parts[1:], default_tf or "1h")
        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "⚡")
        await _send_chart(u.message, ticker, timeframe)
    except Exception as e:
        logger.error(f"[crypto_chart] {e}")


async def crypto_chart_text_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Plain text 'chart btc 1h' (no slash). Silent on failure so normal chat never gets error spam."""
    if not u.message or not u.message.text:
        return
    try:
        parts = u.message.text.split()
        ticker, timeframe = _parse_ticker_tf(parts[1:], "1h")
        await _send_chart(u.message, ticker, timeframe, quiet=True)
    except Exception as e:
        logger.error(f"[crypto_chart_text] {e}")
    raise ApplicationHandlerStop  # message handled -> don't let chat/AI handlers answer it too


# =====================================================================
# /vp  — VOLUME PROFILE (Binance klines, buy/sell split via taker volume)
# Big coins are pre-built in the background so replies are instant.
# =====================================================================
_VP_LOOKBACK = 300                  # candles used for the profile
_VP_BINS = 70                       # price buckets
_VP_VALUE_AREA = 0.70
_VP_COINS = ["BTC", "ETH", "SOL", "XAU"]
_VP_TFS = ["15m", "1h", "4h", "1d"]
_VP_REFRESH = 300                   # background rebuild every 5 min
_VP_TTL = 600                       # cached image considered fresh for 10 min
_VP_MAX_CACHE = 60

_vp_cache = {}                      # (ticker, tf) -> entry dict
_vp_locks = {}
_warm_task = None
_WARM_KEYS = {(c, t) for c in _VP_COINS for t in _VP_TFS}


def _compute_vp(rows, bins: int = _VP_BINS):
    lo = min(r[3] for r in rows)
    hi = max(r[2] for r in rows)
    if hi <= lo:
        hi = lo * 1.0001 + 1e-12
    step = (hi - lo) / bins
    buy = [0.0] * bins
    sell = [0.0] * bins

    for r in rows:
        o, h, l, c, v = r[1], r[2], r[3], r[4], r[5]
        tb = r[6] if len(r) > 6 and r[6] is not None else (v if c >= o else 0.0)
        tb = min(max(tb, 0.0), v)
        sv = v - tb
        i0 = min(bins - 1, max(0, int((l - lo) / step)))
        i1 = min(bins - 1, max(0, int((h - lo) / step)))
        n = i1 - i0 + 1
        for i in range(i0, i1 + 1):
            buy[i] += tb / n
            sell[i] += sv / n

    tot = [b + s for b, s in zip(buy, sell)]
    poc = max(range(bins), key=lambda i: tot[i])
    target = sum(tot) * _VP_VALUE_AREA
    acc, a, b = tot[poc], poc, poc
    while acc < target and (a > 0 or b < bins - 1):
        up = tot[b + 1] if b < bins - 1 else -1
        dn = tot[a - 1] if a > 0 else -1
        if up >= dn:
            b += 1
            acc += tot[b]
        else:
            a -= 1
            acc += tot[a]

    return {
        "lo": lo, "hi": hi, "step": step, "bins": bins,
        "buy": buy, "sell": sell, "tot": tot,
        "poc": poc, "va_lo": a, "va_hi": b,
        "poc_price": lo + (poc + 0.5) * step,
        "vah": lo + (b + 1) * step,
        "val": lo + a * step,
    }


def _render_vp_sync(rows, vp, ticker: str, timeframe: str) -> bytes:
    from PIL import Image, ImageDraw

    rows = sorted(rows, key=lambda r: r[0])
    n = len(rows)

    W, H = 1420, 880
    L, cand_r = 30, 840
    prof_l, prof_r = 860, 1190
    TOP, BOT = 140, 735

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_title, f_sub = _font(36, True), _font(24, True)
    f_lbl, f_tag = _font(17), _font(18, True)

    data_hi = max(r[2] for r in rows)
    data_lo = min(r[3] for r in rows)
    pad = (data_hi - data_lo) * 0.03 or data_hi * 0.001
    hi_ax, lo_ax = data_hi + pad, data_lo - pad

    def py(p):
        return TOP + (hi_ax - p) / (hi_ax - lo_ax) * (BOT - TOP)

    last = rows[-1][4]
    tags = [
        (vp["poc_price"], "POC", _GOLD),
        (vp["vah"], "VAH", _BLUE),
        (vp["val"], "VAL", _BLUE),
        (last, "LAST", _UP if rows[-1][4] >= rows[-1][1] else _DOWN),
    ]
    tag_ys = [py(p) for p, _, _ in tags]

    for i in range(6):
        p = lo_ax + (hi_ax - lo_ax) * i / 5
        y = py(p)
        d.line([(L, y), (prof_r, y)], fill=_GRID, width=1)
        if all(abs(y - ty) > 34 for ty in tag_ys):
            _text(d, (prof_r + 14, y), _fmt_price(p), f_lbl, _TXT, "lm")

    cw = (cand_r - L) / n
    body_half = max(0.5, cw * 0.35)
    for i, (_, o, h, l, c, v, *_rest) in enumerate(rows):
        x = L + (i + 0.5) * cw
        col = _UP if c >= o else _DOWN
        d.line([(x, py(h)), (x, py(l))], fill=col, width=1)
        y1, y2 = py(max(o, c)), py(min(o, c))
        if y2 - y1 < 1:
            y2 = y1 + 1
        d.rectangle([x - body_half, y1, x + body_half, y2], fill=col)

    max_tot = max(vp["tot"]) or 1.0
    pw = prof_r - prof_l
    for i in range(vp["bins"]):
        y_top = py(vp["lo"] + (i + 1) * vp["step"])
        y_bot = py(vp["lo"] + i * vp["step"])
        y1, y2 = y_top + 0.5, y_bot - 0.5
        if y2 - y1 < 1:
            y2 = y1 + 1
        wb = vp["buy"][i] / max_tot * pw
        ws = vp["sell"][i] / max_tot * pw
        in_va = vp["va_lo"] <= i <= vp["va_hi"]
        cb = _UP if in_va else tuple(int(x * 0.45) for x in _UP)
        cs = _DOWN if in_va else tuple(int(x * 0.45) for x in _DOWN)
        if wb >= 0.5:
            d.rectangle([prof_l, y1, prof_l + wb, y2], fill=cb)
        if ws >= 0.5:
            d.rectangle([prof_l + wb, y1, prof_l + wb + ws, y2], fill=cs)
        if i == vp["poc"]:
            d.rectangle([prof_l, y1, prof_l + wb + ws, y2], outline=_GOLD, width=2)

    d.line([(L, py(vp["poc_price"])), (prof_r, py(vp["poc_price"]))], fill=_GOLD, width=2)
    _dashed(d, L, prof_r, py(vp["vah"]), _BLUE)
    _dashed(d, L, prof_r, py(vp["val"]), _BLUE)

    for p, label, col in tags:
        y = py(p)
        s = f"{label}  {_fmt_price(p)}"
        d.rectangle([prof_r + 6, y - 16, prof_r + 6 + d.textlength(s, font=f_tag) + 24, y + 16], fill=col)
        _text(d, (prof_r + 18, y), s, f_tag, (0, 0, 0), "lm")

    fmt = "%d %b" if timeframe in ("1d", "1M") else "%d %b  %H:%M"
    for k in range(5):
        i = min(n - 1, int((k + 0.5) / 5 * n))
        x = L + (i + 0.5) * cw
        lbl = datetime.fromtimestamp(rows[i][0] / 1000, tz=timezone.utc).strftime(fmt)
        _text(d, (min(max(x, L + 55), cand_r - 55), BOT + 20), lbl, f_lbl, _TXT, "ma")
    _text(d, (prof_l, BOT + 20), "Volume Profile", f_lbl, _TXT, "la")

    title = f"{ticker}/USDT"
    _text(d, (L, 72), title, f_title, (255, 255, 255), "ls")
    _text(d, (L + d.textlength(title, font=f_title) + 20, 72), f"Volume Profile  ·  {timeframe}", f_sub, (150, 150, 165), "ls")
    lx = prof_l
    d.rectangle([lx, 102, lx + 18, 120], fill=_UP)
    _text(d, (lx + 30, 111), "Buy", f_lbl, _TXT, "lm")
    d.rectangle([lx + 110, 102, lx + 128, 120], fill=_DOWN)
    _text(d, (lx + 140, 111), "Sell", f_lbl, _TXT, "lm")

    _text(d, (L, H - 52), "powered by - BELUGA.PY", f_lbl, (140, 140, 150), "la")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _build_vp_sync(ticker: str, timeframe: str):
    rows, source = _fetch_ohlcv_sync(ticker, timeframe, _VP_LOOKBACK)
    if not rows or len(rows) < 20:
        return None
    vp = _compute_vp(rows)
    png = _render_vp_sync(rows, vp, ticker, timeframe)
    return {
        "png": png, "ts": time.time(), "source": source,
        "ticker": ticker, "tf": timeframe, "n": len(rows),
        "poc": vp["poc_price"], "vah": vp["vah"], "val": vp["val"],
    }


def _vp_evict():
    if len(_vp_cache) <= _VP_MAX_CACHE:
        return
    victims = sorted((k for k in _vp_cache if k not in _WARM_KEYS), key=lambda k: _vp_cache[k]["ts"])
    for k in victims:
        if len(_vp_cache) <= _VP_MAX_CACHE:
            break
        _vp_cache.pop(k, None)


def _vp_fresh(key) -> bool:
    e = _vp_cache.get(key)
    return bool(e) and (time.time() - e["ts"]) < _VP_TTL


async def _get_vp(ticker: str, timeframe: str, force: bool = False):
    """Returns the cached profile if fresh, else builds it. May return a stale entry if the rebuild fails."""
    key = (ticker, timeframe)
    if not force and _vp_fresh(key):
        return _vp_cache[key]
    lock = _vp_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if not force and _vp_fresh(key):
            return _vp_cache[key]
        loop = asyncio.get_running_loop()
        new = await loop.run_in_executor(None, lambda: _build_vp_sync(ticker, timeframe))
        if new:
            _vp_cache[key] = new
            _vp_evict()
            return new
        return _vp_cache.get(key)


async def vp_warmup_loop():
    """Keeps BTC/ETH/SOL/XAU volume profiles pre-built so /vp replies instantly."""
    await asyncio.sleep(20)  # let the bot finish starting
    while True:
        for coin in _VP_COINS:
            for tf in _VP_TFS:
                try:
                    await _get_vp(coin, tf, force=True)
                except Exception as e:
                    logger.warning(f"[vp_warmup] {coin} {tf}: {str(e)[:80]}")
                await asyncio.sleep(1.5)
        logger.info(f"[vp_warmup] cycle done ({len(_vp_cache)} cached)")
        await asyncio.sleep(_VP_REFRESH)


def start_vp_warmup():
    """Call once inside the running event loop. Also starts the /lh order-book collector."""
    global _warm_task
    if _warm_task is None or _warm_task.done():
        _warm_task = asyncio.create_task(vp_warmup_loop())
        logger.info("[vp_warmup] background task started")
    start_lh_collector()
    return _warm_task


def _vp_caption(e) -> str:
    upd = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%H:%M")
    return (f"📊 *Volume Profile*\n"
            f"`{e['ticker']}/USDT`  ·  `{e['tf']}`\n\n"
            f"🟡 *POC* :  `{_fmt_price(e['poc'])}`\n\n"
            f"🔵 *VAH* :  `{_fmt_price(e['vah'])}`\n"
            f"🔵 *VAL* :  `{_fmt_price(e['val'])}`\n\n"
            f"🕒 *Updated* :  `{upd} UTC`\n\n"
            f"{BRAND}")


async def crypto_vp_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/vp <coin> <tf>  e.g. /vp btc 1h, /vp eth 4h"""
    if not u.message:
        return
    sm = None
    try:
        parts = (u.message.text or "").split()
        ticker, timeframe = _parse_ticker_tf(parts[1:], "1h")
        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "⚡")

        if not _vp_fresh((ticker, timeframe)):
            sm = await u.message.reply_text(
                f"⏳ Building volume profile for *{ticker}/USDT* (`{timeframe}`)...", parse_mode=ParseMode.MARKDOWN
            )

        entry = await asyncio.wait_for(_get_vp(ticker, timeframe), timeout=60)
        if not entry:
            raise ValueError(f"No Binance data for {ticker}/USDT")

        await u.message.reply_photo(photo=_png_file(entry["png"], f"VP_{ticker}_{timeframe}.png"),
                                    caption=_vp_caption(entry), parse_mode=ParseMode.MARKDOWN)
        if sm:
            try:
                await sm.delete()
            except Exception:
                pass
    except BaseException as e:
        _log_exc("[crypto_vp]", e)
        bot_status["error_count"] += 1
        if sm:
            try:
                await sm.edit_text(f"❌ Volume profile error: {type(e).__name__}: {str(e)[:150]}")
            except Exception:
                pass
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise


# =====================================================================
# Callbacks. New charts only have the Refresh button; ob_/fp_/ind_ branches
# stay so buttons on OLD chart messages don't break.
# =====================================================================
def _fetch_orderbook_sync(ticker: str):
    for host in _BINANCE_HOSTS:
        try:
            r = requests.get(f"{host}/api/v3/depth", params={"symbol": f"{ticker}USDT", "limit": 10},
                             headers=_HTTP_UA, timeout=8)
            if r.status_code == 200:
                d = r.json()
                bids = [(float(p), float(a)) for p, a in d.get("bids", [])]
                asks = [(float(p), float(a)) for p, a in d.get("asks", [])]
                if bids and asks:
                    return bids, asks, "Binance"
        except Exception:
            continue
    if exchange is not None:
        ob = exchange.fetch_order_book(f"{ticker}/USDT", limit=10)
        return ob.get('bids', []), ob.get('asks', []), str(getattr(exchange, "name", "Exchange"))
    raise ValueError("no orderbook source available")


async def fetch_orderbook_data(ticker: str):
    """Fetches live orderbook (bids and asks)."""
    try:
        loop = asyncio.get_running_loop()
        bids, asks, source = await loop.run_in_executor(None, lambda: _fetch_orderbook_sync(ticker))

        text = f"📚 *LIVE ORDERBOOK: {ticker}/USDT*\n━━━━━━━━━━━━━━━━━━━━\n\n🔴 *ASKS (Sell Orders)*\n"
        for price, amt in reversed(asks[:5]):
            text += f"`{_fmt_price(price)}  |  {amt:,.4f}`\n"

        text += "\n🟢 *BIDS (Buy Orders)*\n"
        for price, amt in bids[:5]:
            text += f"`{_fmt_price(price)}  |  {amt:,.4f}`\n"

        text += f"\n━━━━━━━━━━━━━━━━━━━━\n{BRAND}"
        return text
    except Exception as e:
        return f"❌ Could not fetch orderbook for {ticker}: {str(e)[:150]}"


def _fetch_aggtrades_sync(ticker: str):
    for host in _BINANCE_HOSTS:
        try:
            r = requests.get(f"{host}/api/v3/aggTrades", params={"symbol": f"{ticker}USDT", "limit": 50},
                             headers=_HTTP_UA, timeout=8)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


async def fetch_footprint_summary(ticker: str):
    """Calculates basic volume footprint delta / recent aggressive buys/sells from Binance aggTrades."""
    try:
        loop = asyncio.get_running_loop()
        trades = await loop.run_in_executor(None, lambda: _fetch_aggtrades_sync(ticker))
        if not trades:
            return f"❌ Footprint data unavailable for {ticker}"

        buy_vol = sum(float(t['q']) for t in trades if t['m'] is False)
        sell_vol = sum(float(t['q']) for t in trades if t['m'] is True)
        total_vol = buy_vol + sell_vol
        delta = buy_vol - sell_vol

        delta_sign = "🟢 +" if delta >= 0 else "🔴 "
        text = (f"🗺 *VOLUME FOOTPRINT (Recent Delta)*: `{ticker}/USDT`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🟢 *Aggressive Buy Vol*: `{buy_vol:,.2f}`\n"
                f"🔴 *Aggressive Sell Vol*: `{sell_vol:,.2f}`\n"
                f"⚖️ *Delta*: `{delta_sign}{delta:,.2f}`\n"
                f"📊 *Total Traded*: `{total_vol:,.2f} {ticker}`\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n{BRAND}")
        return text
    except Exception as e:
        return f"❌ Error computing footprint: {str(e)[:150]}"


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    data = query.data or ""

    try:
        if data.startswith("chart_"):
            # Refresh: edit the same photo in place; fall back to a new photo if Telegram refuses
            _, ticker, tf = data.split("_", 2)
            ticker = _clean_ticker(ticker)
            tf = tf if tf in _TF_MAPPING.values() else "1h"
            png, last = await _make_chart(ticker, tf)
            caption = _chart_caption(ticker, tf, last)
            try:
                await query.message.edit_media(
                    media=InputMediaPhoto(media=_png_file(png, f"{ticker}_{tf}.png"),
                                          caption=caption, parse_mode=ParseMode.MARKDOWN),
                    reply_markup=_chart_keyboard(ticker, tf),
                )
            except Exception as e:
                if "not modified" in str(e).lower():
                    return
                await query.message.reply_photo(photo=_png_file(png, f"{ticker}_{tf}.png"),
                                                caption=caption, parse_mode=ParseMode.MARKDOWN,
                                                reply_markup=_chart_keyboard(ticker, tf))

        elif data.startswith("ob_"):
            ticker = _clean_ticker(data.split("_", 1)[1])
            await query.message.reply_text(await fetch_orderbook_data(ticker), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("fp_"):
            ticker = _clean_ticker(data.split("_", 1)[1])
            await query.message.reply_text(await fetch_footprint_summary(ticker), parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("ind_"):
            _, ticker, tf = data.split("_", 2)
            await query.message.reply_text(
                f"📊 Indicator Options for *{ticker}* (`{tf}`):\n• RSI (Relative Strength Index)\n"
                f"• Moving Averages (EMA/SMA)\n• Bollinger Bands\n\n"
                f"*(Custom indicator toggles enabled via Beluga Engine)*",
                parse_mode=ParseMode.MARKDOWN)
    except BaseException as e:
        _log_exc("[button_callback]", e)
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise


# =====================================================================
# /vf and /lh — shared helpers
# =====================================================================
_MUTED = (140, 140, 150)
_WHITE = (255, 255, 255)
_SUBTLE = (150, 150, 165)


def _fmt_usd(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.2f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.1f}K"
    return f"${v:,.0f}"


def _fmt_cell(v: float) -> str:
    """Very compact number for a footprint cell: 1.2K / 345 / 12.5 / 0.45"""
    if v >= 1000:
        return f"{v / 1000:.1f}K"
    if v >= 100:
        return f"{v:.0f}"
    if v >= 10:
        return f"{v:.1f}"
    if v >= 1:
        return f"{v:.1f}"
    return f"{v:.2f}"


def _vtext(img, cx, cy, s, font, fill):
    """Text rotated 90° (reads bottom -> top), centred on (cx, cy)."""
    from PIL import Image, ImageDraw
    l, t, r, b = ImageDraw.Draw(img).textbbox((0, 0), s, font=font)
    layer = Image.new("RGBA", (r + 6, b + 8), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((3, 3), s, font=font, fill=tuple(fill) + (255,))
    rot = layer.rotate(90, expand=True)
    img.paste(rot, (int(cx - rot.width / 2), int(cy - rot.height / 2)), rot)


# =====================================================================
# /vf — VOLUME FOOTPRINT (last 40 candles by default)
#   /vf btc 1h   /vf eth 4h   /vf btc 1h 20   (10-60 candles; <=20 also prints numbers inside cells)
# Each candle = red half (sell volume) | green half (buy volume) per price level,
# a gold box on the candle's POC, a white box on open-close, and a Buy/Sell/Delta
# table underneath. Built from 1m/5m/15m candles' taker buy/sell volume (Binance).
# =====================================================================
_VF_DEFAULT_N = 40
_VF_MIN_N, _VF_MAX_N = 10, 60
_VF_SUB = {"5m": "1m", "15m": "1m", "1h": "1m", "4h": "5m", "1d": "15m"}
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


def _compute_vf(ticker: str, tf: str, n_req: int):
    parents, _src = _fetch_ohlcv_sync(ticker, tf, n_req)
    if not parents or len(parents) < 10:
        return None
    parents = sorted(parents, key=lambda r: r[0])[-n_req:]
    n = len(parents)
    pms = _TF_MS[tf]
    subs = _fetch_klines_range(ticker, _VF_SUB[tf], parents[0][0], parents[-1][0] + pms)
    if len(subs) < n:
        return None

    lo = min(p[3] for p in parents)
    hi = max(p[2] for p in parents)
    if hi <= lo:
        hi = lo * 1.0001 + 1e-12
    pad = (hi - lo) * 0.03
    lo, hi = lo - pad, hi + pad
    bins = 36 if n <= 20 else 56
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

    cbuy = [sum(r) for r in buy]
    csell = [sum(r) for r in sell]
    return {"parents": parents, "buy": buy, "sell": sell, "lo": lo, "hi": hi, "step": step, "bins": bins,
            "cbuy": cbuy, "csell": csell, "cdelta": [b - s for b, s in zip(cbuy, csell)]}


def _render_vf_sync(vf, ticker: str, tf: str) -> bytes:
    from PIL import Image, ImageDraw

    parents, buy, sell = vf["parents"], vf["buy"], vf["sell"]
    n, bins, lo, hi, step = len(parents), vf["bins"], vf["lo"], vf["hi"], vf["step"]
    numbers = n <= 20

    W, H = 1600, 1010
    L, R = 90, 170
    TOP, P_BOT = 140, 640
    ROW_H = 66
    T_TOP = P_BOT + 24
    plot_r = W - R
    cw = (plot_r - L) / n

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_title, f_sub = _font(36, True), _font(24, True)
    f_lbl, f_tag, f_row = _font(17), _font(18, True), _font(17, True)
    f_cell, f_tab, f_tab_s = _font(11), _font(14, True), _font(12, True)

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

    side_vals = sorted(v for rows in (buy, sell) for r in rows for v in r if v > 0)
    ref = (side_vals[int(len(side_vals) * 0.95)] if side_vals else 1.0) or 1.0

    for i in range(n):
        o, c = parents[i][1], parents[i][4]
        x0, x1 = L + i * cw + 1.5, L + (i + 1) * cw - 1.5
        xm = (x0 + x1) / 2
        best_j, best_t = -1, 0.0
        for j in range(bins):
            b, s = buy[i][j], sell[i][j]
            if b <= 0 and s <= 0:
                continue
            y0, y1 = py(lo + (j + 1) * step) + 0.5, py(lo + j * step) - 0.5
            if y1 - y0 < 1:
                y1 = y0 + 1
            for v, xa, xb, base in ((s, x0, xm - 0.5, _DOWN), (b, xm + 0.5, x1, _UP)):
                if v <= 0:
                    continue
                a = 0.18 + 0.82 * min(1.0, (v / ref) ** 0.6)
                col = tuple(int(_BG[k] + (base[k] - _BG[k]) * a) for k in range(3))
                d.rectangle([xa, y0, xb, y1], fill=col)
                if numbers and y1 - y0 >= 11:
                    _text(d, ((xa + xb) / 2, (y0 + y1) / 2), _fmt_cell(v), f_cell,
                          (0, 0, 0) if a > 0.72 else (235, 235, 235), "mm")
            if b + s > best_t:
                best_t, best_j = b + s, j
        if best_j >= 0:
            y0, y1 = py(lo + (best_j + 1) * step), py(lo + best_j * step)
            d.rectangle([x0, y0, x1, max(y0 + 1, y1 - 1)], outline=_GOLD, width=1)
        by0, by1 = py(max(o, c)), py(min(o, c))
        d.rectangle([x0 - 1, by0, x1 + 1, max(by0 + 1, by1)], outline=(225, 225, 230), width=1)

    # Buy / Sell / Delta table (one rotated number per candle)
    table = (("Buy", vf["cbuy"], _UP), ("Sell", vf["csell"], _DOWN), ("Delta", vf["cdelta"], _WHITE))
    for r, (label, arr, lcol) in enumerate(table):
        ytop = T_TOP + r * ROW_H
        yc = ytop + ROW_H / 2
        d.line([(L, ytop), (plot_r, ytop)], fill=_GRID, width=1)
        _text(d, (L - 14, yc), label, f_row, lcol, "rm")
        for i, v in enumerate(arr):
            if label == "Delta":
                s, col = ("+" if v >= 0 else "") + _fmt_vol(v), (_UP if v >= 0 else _DOWN)
            else:
                s, col = _fmt_vol(v), lcol
            f = f_tab if d.textlength(s, font=f_tab) <= ROW_H - 12 else f_tab_s
            _vtext(img, L + (i + 0.5) * cw, yc, s, f, col)
    d.line([(L, T_TOP + 3 * ROW_H), (plot_r, T_TOP + 3 * ROW_H)], fill=_GRID, width=1)

    # time labels: HH:MM, date underneath when the day changes
    y_lbl = T_TOP + 3 * ROW_H + 24
    every = max(1, math.ceil(52 / cw))
    prev_day = None
    for i in sorted(range(n - 1, -1, -every)):
        dt = datetime.fromtimestamp(parents[i][0] / 1000, tz=timezone.utc)
        x = L + (i + 0.5) * cw
        if tf == "1d":
            _text(d, (x, y_lbl), dt.strftime("%d %b"), f_lbl, _TXT, "ma")
            continue
        _text(d, (x, y_lbl), dt.strftime("%H:%M"), f_lbl, _TXT, "ma")
        if dt.day != prev_day:
            _text(d, (x, y_lbl + 26), dt.strftime("%d %b"), f_lbl, _GOLD, "ma")
            prev_day = dt.day

    d.line([(L, ly), (plot_r, ly)], fill=lc, width=1)
    tag = _fmt_price(last)
    d.rectangle([plot_r + 4, ly - 16, plot_r + 4 + d.textlength(tag, font=f_tag) + 24, ly + 16], fill=lc)
    _text(d, (plot_r + 16, ly), tag, f_tag, (0, 0, 0), "lm")

    title = f"{ticker}/USDT"
    _text(d, (L - 60, 72), title, f_title, _WHITE, "ls")
    _text(d, (L - 60 + d.textlength(title, font=f_title) + 20, 72), f"Volume Footprint  ·  {tf}", f_sub, _SUBTLE, "ls")
    lx = L - 60
    for col, label in ((_DOWN, "Sell volume"), (_UP, "Buy volume"), (_GOLD, "POC of candle")):
        d.rectangle([lx, 104, lx + 18, 122], fill=col)
        _text(d, (lx + 30, 113), label, f_lbl, _TXT, "lm")
        lx += 30 + d.textlength(label, font=f_lbl) + 46
    _text(d, (L - 60, H - 52), "powered by - BELUGA.PY", f_lbl, _MUTED, "la")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _build_vf_sync(ticker: str, tf: str, n: int):
    vf = _compute_vf(ticker, tf, n)
    if not vf:
        return None
    return {"png": _render_vf_sync(vf, ticker, tf), "ts": time.time(), "ticker": ticker, "tf": tf,
            "buy": sum(vf["cbuy"]), "sell": sum(vf["csell"]),
            "lbuy": vf["cbuy"][-1], "lsell": vf["csell"][-1]}


async def _get_vf(ticker: str, tf: str, n: int):
    key = (ticker, tf, n)
    e = _vf_cache.get(key)
    if e and time.time() - e["ts"] < _VF_TTL:
        return e
    async with _vf_locks.setdefault(key, asyncio.Lock()):
        e = _vf_cache.get(key)
        if e and time.time() - e["ts"] < _VF_TTL:
            return e
        loop = asyncio.get_running_loop()
        new = await loop.run_in_executor(None, lambda: _build_vf_sync(ticker, tf, n))
        if new:
            _vf_cache[key] = new
            if len(_vf_cache) > 30:
                for k in sorted(_vf_cache, key=lambda k: _vf_cache[k]["ts"])[:10]:
                    _vf_cache.pop(k, None)
            return new
        return e


def _signed(v: float) -> str:
    return ("+" if v >= 0 else "-") + _fmt_vol(abs(v))


def _vf_caption(e) -> str:
    upd = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%H:%M")
    return (f"🗺 *Volume Footprint*\n"
            f"`{e['ticker']}/USDT`  ·  `{e['tf']}`\n\n"
            f"🕯 *Current Candle*\n"
            f"🟢 Buy :  `{_fmt_vol(e['lbuy'])}`\n"
            f"🔴 Sell :  `{_fmt_vol(e['lsell'])}`\n"
            f"⚖️ Delta :  `{_signed(e['lbuy'] - e['lsell'])}`\n\n"
            f"📊 *Total*\n"
            f"🟢 Buy :  `{_fmt_vol(e['buy'])}`\n"
            f"🔴 Sell :  `{_fmt_vol(e['sell'])}`\n"
            f"⚖️ Delta :  `{_signed(e['buy'] - e['sell'])}`\n\n"
            f"🕒 *Updated* :  `{upd} UTC`\n\n"
            f"{BRAND}")


def _parse_vf(tokens):
    ticker, tf, n = "BTC", "1h", _VF_DEFAULT_N
    rest = list(tokens)
    if rest and not rest[0].isdigit() and not _tf_of(rest[0]):
        ticker = _clean_ticker(rest.pop(0))
    for tok in rest:
        if _tf_of(tok):
            tf = _tf_of(tok)
        elif tok.isdigit():
            n = max(_VF_MIN_N, min(_VF_MAX_N, int(tok)))
    return ticker, tf, n


async def crypto_vf_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/vf <coin> <tf> [candles]   e.g. /vf btc 1h, /vf btc 4h, /vf eth 1h 20"""
    if not u.message:
        return
    sm = None
    try:
        ticker, tf, n = _parse_vf((u.message.text or "").split()[1:])
        if tf not in _VF_SUB:
            await u.message.reply_text("⚠️ Footprint timeframes:\n\n`5m`  `15m`  `1h`  `4h`  `1d`",
                                       parse_mode=ParseMode.MARKDOWN)
            return
        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "⚡")

        e = _vf_cache.get((ticker, tf, n))
        if not (e and time.time() - e["ts"] < _VF_TTL):
            sm = await u.message.reply_text(
                f"⏳ Building footprint for *{ticker}/USDT* (`{tf}`)...", parse_mode=ParseMode.MARKDOWN)
        e = await asyncio.wait_for(_get_vf(ticker, tf, n), timeout=90)
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
# /lh — LIQUIDITY HEATMAP (order-book depth collected over time), default 1D
# A background task snapshots the Binance order book every ~30s and keeps 26h.
# The heatmap = those snapshots stacked in time, with thin candles on top.
# Only clearly large resting orders are coloured; small noise stays dark (clean look).
# History is saved to MongoDB every ~5 min and restored after a restart/deploy.
# =====================================================================
_LH_CORE = ["BTC", "ETH", "SOL", "XAU"]
_LH_INTERVAL = 30
_LH_KEEP_SEC = 26 * 3600
_LH_EXTRA_MAX = 4
_LH_EXTRA_IDLE = 6 * 3600
_LH_WINDOWS = {"1h": 1, "2h": 2, "4h": 4, "6h": 6, "12h": 12, "1d": 24, "24h": 24}
_LH_DEFAULT_H = 24
_LH_TTL = 45

_lh_widths = {}      # ticker -> price bucket width
_lh_hist = {}        # ticker -> deque of (ts, mid, array('i') bucket idx, array('f') usd notional)
_lh_extra = {}       # ticker -> last requested ts (non-core coins being tracked)
_lh_cache = {}
_lh_task = None

_CMAP = [(0.0, (16, 18, 36)), (0.25, (28, 52, 110)), (0.55, (32, 140, 170)),
         (0.80, (120, 210, 150)), (1.0, (250, 225, 90))]


def _cmap(x: float):
    x = 0.0 if x < 0 else 1.0 if x > 1 else x
    for (a, ca), (b, cb) in zip(_CMAP, _CMAP[1:]):
        if x <= b:
            t = (x - a) / (b - a) if b > a else 0.0
            return tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
    return _CMAP[-1][1]


_LUT = [_cmap(i / 255) for i in range(256)]


def _win_label(h: int) -> str:
    return "1D" if h == 24 else f"{h}h"


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


# ---- MongoDB (shared helper, also used by trades.py) -------------------------------------------
_mdb_cache = None
_mdb_fail_ts = 0.0


def _find_mongo_uri():
    """Finds the Mongo connection string without knowing its variable name: well-known names first,
    then any env var / config.py string that starts with mongodb:// or mongodb+srv://."""
    for k in ("MONGO_URI", "MONGODB_URI", "MONGO_URL", "MONGODB_URL", "MONGO_CONNECTION_STRING", "DATABASE_URL"):
        v = os.environ.get(k)
        if isinstance(v, str) and v.startswith(("mongodb://", "mongodb+srv://")):
            return v
    for v in os.environ.values():
        if isinstance(v, str) and v.startswith(("mongodb://", "mongodb+srv://")):
            return v
    try:
        import config as _cfg
        for name in dir(_cfg):
            val = getattr(_cfg, name, None)
            if isinstance(val, str) and val.startswith(("mongodb://", "mongodb+srv://")):
                return val
    except Exception:
        pass
    return None


async def _mdb():
    """Returns the Motor database (cached) or None when Mongo is not reachable. Retries every 10 min."""
    global _mdb_cache, _mdb_fail_ts
    if _mdb_cache is not None:
        return _mdb_cache
    if time.time() - _mdb_fail_ts < 600:
        return None
    uri = _find_mongo_uri()
    if not uri:
        _mdb_fail_ts = time.time()
        logger.warning("[mongo] no connection string found in env/config - using local files only")
        return None
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
        try:
            db = client.get_default_database()
        except Exception:
            name = next((os.environ.get(k) for k in ("MONGO_DB", "MONGODB_DB", "MONGO_DB_NAME", "DB_NAME") if os.environ.get(k)), "beluga")
            db = client[name]
        await client.admin.command("ping")
        _mdb_cache = db
        logger.info(f"[mongo] trades/heatmap storage connected (db={db.name})")
        return db
    except Exception as e:
        _mdb_fail_ts = time.time()
        logger.warning(f"[mongo] connect failed: {str(e)[:100]}")
        return None


def _pack(snaps) -> bytes:
    meta = [[s[0], s[1], len(s[2])] for s in snaps]
    head = json.dumps(meta).encode()
    body = b"".join(s[2].tobytes() + s[3].tobytes() for s in snaps)
    return zlib.compress(len(head).to_bytes(4, "big") + head + body, 6)


def _unpack(blob: bytes):
    raw = zlib.decompress(blob)
    hl = int.from_bytes(raw[:4], "big")
    pos = 4 + hl
    out = []
    for ts, mid, n in json.loads(raw[4:pos]):
        ks, vs = array("i"), array("f")
        ks.frombytes(raw[pos:pos + n * ks.itemsize])
        pos += n * ks.itemsize
        vs.frombytes(raw[pos:pos + n * vs.itemsize])
        pos += n * vs.itemsize
        out.append((ts, mid, ks, vs))
    return out


_lh_pending = {}          # ticker -> snapshots not yet written to Mongo
_LH_FLUSH_EVERY = 300     # write a compressed chunk per coin every 5 min


def _store_snapshot(ticker: str, snap):
    dq = _lh_hist.setdefault(ticker, deque())
    dq.append(snap)
    cutoff = snap[0] - _LH_KEEP_SEC
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    pend = _lh_pending.setdefault(ticker, [])
    pend.append(snap)
    if len(pend) > 600:
        del pend[:len(pend) - 600]


async def lh_flush():
    """Writes pending order-book snapshots (+ bucket widths) to Mongo. Old chunks expire automatically (TTL)."""
    db = await _mdb()
    if db is None:
        return
    try:
        col = db["lh_chunks"]
        if not getattr(lh_flush, "_indexed", False):
            await col.create_index("ts_dt", expireAfterSeconds=int(_LH_KEEP_SEC + 3600))
            await col.create_index([("coin", 1), ("ts_start", 1)])
            lh_flush._indexed = True
        from bson import Binary
        for coin, snaps in list(_lh_pending.items()):
            if not snaps:
                continue
            batch = snaps[:]
            await col.insert_one({"coin": coin, "ts_start": batch[0][0], "ts_end": batch[-1][0],
                                  "ts_dt": datetime.fromtimestamp(batch[-1][0], tz=timezone.utc),
                                  "n": len(batch), "blob": Binary(_pack(batch))})
            del snaps[:len(batch)]
        await db["lh_meta"].update_one({"_id": "widths"}, {"$set": {"w": dict(_lh_widths)}}, upsert=True)
    except Exception as e:
        logger.warning(f"[lh_flush] {str(e)[:100]}")


async def lh_save_now():
    """Call on shutdown so a deploy loses at most a few seconds of heatmap history."""
    await lh_flush()


async def lh_restore():
    """Loads the last 26h of heatmap snapshots from Mongo after a restart/deploy."""
    db = await _mdb()
    if db is None:
        return
    try:
        meta = await db["lh_meta"].find_one({"_id": "widths"})
        if meta:
            for k, v in (meta.get("w") or {}).items():
                _lh_widths.setdefault(k, v)
        cutoff = time.time() - _LH_KEEP_SEC
        total = 0
        async for doc in db["lh_chunks"].find({"ts_end": {"$gte": cutoff}}).sort("ts_start", 1):
            coin = doc.get("coin")
            if coin not in _LH_CORE:
                continue
            dq = _lh_hist.setdefault(coin, deque())
            for s in _unpack(bytes(doc["blob"])):
                if s[0] >= cutoff and (not dq or s[0] > dq[-1][0]):
                    dq.append(s)
                    total += 1
        logger.info(f"[lh_restore] {total} snapshots restored from Mongo")
    except Exception as e:
        logger.warning(f"[lh_restore] {str(e)[:100]}")


async def lh_collector_loop():
    """Keeps order-book snapshots flowing for BTC/ETH/SOL/XAU (+ any coin users asked for);
    history is restored from Mongo at start and saved back every few minutes."""
    loop = asyncio.get_running_loop()
    await lh_restore()
    await asyncio.sleep(15)
    last_flush = time.time()
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
            _lh_pending.pop(coin, None)
        if time.time() - last_flush >= _LH_FLUSH_EVERY:
            await lh_flush()
            last_flush = time.time()
        await asyncio.sleep(max(5.0, _LH_INTERVAL - (time.time() - t0)))


def start_lh_collector():
    """Starts the order-book collector once (start_vp_warmup() already calls this)."""
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
    want = span_sec / 60 / 100
    for mins, name in ((1, "1m"), (3, "3m"), (5, "5m"), (15, "15m"), (30, "30m"), (60, "1h")):
        if mins >= want:
            return mins * 60, name
    return 3600, "1h"


def _dashed_line(d, x1, x2, y, color=(255, 255, 255), dash=10, gap=6):
    x = x1
    while x < x2:
        d.line([(x, y), (min(x + dash, x2), y)], fill=color, width=1)
        x += dash + gap


def _render_lh_sync(ticker: str, window_h: int, snaps, candles, interval_sec: int, w: float) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

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
    pad = (yhi - ylo) * 0.12 + mid_last * 0.001
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
    last, last_c = None, -10 ** 9
    max_gap = max(2, int(900 / (span / ncols)))
    for c in range(ncols):
        if cols[c] is None:
            if last is not None and c - last_c <= max_gap:
                cols[c] = last
        else:
            last, last_c = cols[c], c

    # clean look: small orders stay dark, only clearly large resting orders glow
    vals.sort()
    floor = vals[int(len(vals) * 0.55)] if vals else 0.0
    top = vals[int(len(vals) * 0.985)] if vals else 1.0
    if top <= floor:
        top = floor * 1.5 + 1.0
    bg = _LUT[0]
    pix = []
    for r in range(nrows - 1, -1, -1):
        for c in range(ncols):
            col = cols[c]
            v = col.get(r, 0.0) if col else 0.0
            if v <= floor:
                pix.append(bg)
            else:
                x = min(1.0, (v - floor) / (top - floor)) ** 1.5
                pix.append(_LUT[int(x * 255)])

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
    hm = hm.resize((plot_w, plot_h), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(1.1))
    img.paste(hm, (L, TOP))

    def py(p):
        return TOP + (hi_ax - p) / (hi_ax - lo_ax) * plot_h

    def px(ts_sec):
        return L + (ts_sec - t0) / span * plot_w

    # thin candles (white = up, dark with white outline = down)
    cwpx = min(7.0, max(2.0, interval_sec / span * plot_w * 0.55))
    for ts, o, h, l, c, _v, *_r in cand:
        x = px(ts / 1000 + interval_sec / 2)
        if x < L - cwpx or x > plot_r + cwpx:
            continue
        d.line([(x, py(h)), (x, py(l))], fill=(225, 225, 232), width=1)
        y1, y2 = py(max(o, c)), py(min(o, c))
        if y2 - y1 < 1:
            y2 = y1 + 1
        up = c >= o
        d.rectangle([x - cwpx / 2, y1, x + cwpx / 2, y2],
                    fill=(240, 240, 245) if up else (12, 12, 18), outline=(240, 240, 245))

    ly = py(mid_last)
    for i in range(7):
        p = lo_ax + (hi_ax - lo_ax) * i / 6
        y = py(p)
        d.line([(plot_r, y), (plot_r + 8, y)], fill=_TXT, width=1)
        if abs(y - ly) > 32:
            _text(d, (plot_r + 16, y), _fmt_price(p), f_lbl, _TXT, "lm")
    tfmt = "%H:%M" if span <= 20 * 3600 else "%d %b  %H:%M"
    for k in range(5):
        ts = t0 + span * (k + 0.5) / 5
        _text(d, (px(ts), BOT + 22), datetime.fromtimestamp(ts, tz=timezone.utc).strftime(tfmt), f_lbl, _TXT, "ma")

    _dashed_line(d, L, plot_r, ly)
    tag = _fmt_price(mid_last)
    d.rectangle([plot_r + 6, ly - 16, plot_r + 6 + d.textlength(tag, font=f_tag) + 24, ly + 16], fill=_WHITE)
    _text(d, (plot_r + 18, ly), tag, f_tag, (0, 0, 0), "lm")

    bx0, bx1 = 1415, 1440
    for y in range(TOP, BOT):
        d.line([(bx0, y), (bx1, y)], fill=_LUT[int((1 - (y - TOP) / plot_h) * 255)], width=1)
    _text(d, ((bx0 + bx1) / 2, TOP - 16), "High", f_lbl, _TXT, "ms")
    _text(d, ((bx0 + bx1) / 2, BOT + 32), "Low", f_lbl, _TXT, "ms")

    title = f"{ticker}/USDT"
    _text(d, (L, 72), title, f_title, _WHITE, "ls")
    _text(d, (L + d.textlength(title, font=f_title) + 20, 72), f"Liquidity Heatmap  ·  {_win_label(window_h)}", f_sub, _SUBTLE, "ls")
    _text(d, (L, 112), "Bright bands = large resting orders", f_lbl, _MUTED, "lm")
    _text(d, (L, H - 52), "powered by - BELUGA.PY", f_lbl, _MUTED, "la")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
    if e["span"] < e["window"] * 3600 * 0.9:
        hrs, mins = int(e["span"] // 3600), int(e["span"] % 3600 // 60)
        have = f"{hrs}h {mins}m" if hrs else f"{mins}m"
        note = f"⏳ Abhi {have} ka data jama hua hai, {_win_label(e['window'])} poora hone me time lagega.\n\n"
    return (f"🔥 *Liquidity Heatmap*\n"
            f"`{e['ticker']}/USDT`  ·  `{_win_label(e['window'])}`\n\n"
            f"🟢 *Biggest Bid Wall* :  `{_fmt_price(e['bid'][0])}`  ·  `{_fmt_usd(e['bid'][1])}`\n\n"
            f"🔴 *Biggest Ask Wall* :  `{_fmt_price(e['ask'][0])}`  ·  `{_fmt_usd(e['ask'][1])}`\n\n"
            f"{note}"
            f"🕒 *Updated* :  `{upd} UTC`\n\n"
            f"{BRAND}")


def _parse_lh(tokens):
    if not tokens:
        return "BTC", _LH_DEFAULT_H
    if len(tokens) == 1 and tokens[0].lower() in _LH_WINDOWS:
        return "BTC", _LH_WINDOWS[tokens[0].lower()]
    ticker = _clean_ticker(tokens[0])
    window = next((_LH_WINDOWS[t.lower()] for t in tokens[1:] if t.lower() in _LH_WINDOWS), _LH_DEFAULT_H)
    return ticker, window


async def crypto_lh_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/lh <coin> [window]  e.g. /lh btc (default 1D), /lh eth 4h   (window: 1h 2h 4h 6h 12h 1d)"""
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

        await u.message.reply_photo(photo=_png_file(e["png"], f"LH_{ticker}_{_win_label(window)}.png"),
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
