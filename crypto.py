import asyncio
import io
import re
import time
import traceback
from datetime import datetime, timezone

import ccxt
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import texts as T
from config import logger
from state import bot_status, cache_movers
from utils import safe_react

# =====================================================================
# REGISTRATION (bot.py) — add these:
#
#   from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters
#   app.add_handler(CommandHandler("chart", crypto_chart_handler))
#   app.add_handler(CommandHandler("vp", crypto_vp_handler))
#   app.add_handler(MessageHandler(filters.Regex(CHART_TEXT_REGEX) & ~filters.COMMAND, crypto_chart_text_handler))
#   (button_callback_handler stays registered as before)
#
#   # right after `await init_exchange_async()`:
#   start_vp_warmup()
# =====================================================================

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


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _dashed(d, x1, x2, y, color, dash=8, gap=5):
    x = x1
    while x < x2:
        d.line([(x, y), (min(x + dash, x2), y)], fill=color, width=1)
        x += dash + gap


# =====================================================================
# /chart  — candles + volume, Refresh button only
# =====================================================================
def _render_chart_sync(rows, ticker: str, timeframe: str) -> bytes:
    """Candles + volume drawn straight onto a PIL image."""
    from PIL import Image, ImageDraw

    rows = sorted((r[:6] for r in rows), key=lambda r: r[0])
    n = len(rows)

    W, H = 1200, 780
    L, R, TOP = 20, 110, 70
    price_bottom = 530
    vol_top, vol_bottom = 550, 690
    plot_w = W - L - R

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_sm, f_title = _font(15), _font(24)

    max_h = max(r[2] for r in rows)
    min_l = min(r[3] for r in rows)
    span = (max_h - min_l) or (max_h * 0.01) or 1e-9
    hi, lo = max_h + span * 0.05, min_l - span * 0.05
    max_v = max(r[5] for r in rows) or 1.0

    def py(p):
        return TOP + (hi - p) / (hi - lo) * (price_bottom - TOP)

    def text_w(s, f):
        return d.textlength(s, font=f)

    for i in range(6):
        p = lo + (hi - lo) * i / 5
        y = py(p)
        d.line([(L, y), (W - R, y)], fill=_GRID, width=1)
        d.text((W - R + 8, y - 8), _fmt_price(p), fill=_TXT, font=f_sm)

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

    d.line([(L, vol_bottom), (W - R, vol_bottom)], fill=_GRID, width=1)
    d.text((W - R + 8, vol_top), "Volume", fill=_TXT, font=f_sm)

    fmt = "%d %b" if timeframe in ("1d", "1M") else "%d %b %H:%M"
    step = max(1, n // 6)
    for i in range(0, n, step):
        x = L + (i + 0.5) * cw
        lbl = datetime.fromtimestamp(rows[i][0] / 1000, tz=timezone.utc).strftime(fmt)
        d.text((max(4, x - text_w(lbl, f_sm) / 2), vol_bottom + 10), lbl, fill=_TXT, font=f_sm)

    last = rows[-1][4]
    ly = py(last)
    lc = _UP if rows[-1][4] >= rows[-1][1] else _DOWN
    d.line([(L, ly), (W - R, ly)], fill=lc, width=1)
    tag = _fmt_price(last)
    d.rectangle([W - R + 2, ly - 11, W - R + 8 + text_w(tag, f_sm) + 6, ly + 11], fill=lc)
    d.text((W - R + 8, ly - 8), tag, fill=(0, 0, 0), font=f_sm)
    d.text((L, 20), f"{ticker}/USDT  ·  {timeframe}  ·  {tag}", fill=(255, 255, 255), font=f_title)
    d.text((L, H - 34), "powered by - BELUGA.PY", fill=(150, 150, 150), font=f_sm)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _make_chart(ticker: str, timeframe: str):
    """Returns (png_bytes, source). Raises on failure."""
    loop = asyncio.get_running_loop()
    rows, source = await loop.run_in_executor(None, lambda: _fetch_ohlcv_sync(ticker, timeframe, 60))
    if not rows:
        raise ValueError(f"No candle data for {ticker}/USDT")
    png = await loop.run_in_executor(None, lambda: _render_chart_sync(rows, ticker, timeframe))
    return png, source


def _chart_keyboard(ticker: str, timeframe: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"chart_{ticker}_{timeframe}")]])


def _chart_caption(ticker: str, timeframe: str, source: str) -> str:
    return (f"⚡ *{ticker}/USDT* · `{timeframe}`\n"
            f"📡 {source}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{BRAND}")


async def _send_chart(message, ticker: str, timeframe: str, quiet: bool = False):
    """Used by /chart, plain-text 'chart btc 1h' and nothing else. quiet=True -> no status/error messages."""
    sm = None
    if not quiet:
        sm = await message.reply_text(
            f"⚡ Generating chart for *{ticker}/USDT* (`{timeframe}`)...", parse_mode=ParseMode.MARKDOWN
        )
    try:
        png, source = await _make_chart(ticker, timeframe)
        if sm:
            try:
                await sm.delete()
            except Exception:
                pass
        await message.reply_photo(photo=_png_file(png, f"{ticker}_{timeframe}.png"),
                                  caption=_chart_caption(ticker, timeframe, source),
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
        ticker, timeframe = _parse_ticker_tf(parts[1:], "1h")
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

    W, H = 1300, 800
    L, cand_r = 20, 800
    prof_l, prof_r = 815, 1130
    TOP, BOT = 100, 700

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_sm, f_title = _font(15), _font(26)

    data_hi = max(r[2] for r in rows)
    data_lo = min(r[3] for r in rows)
    pad = (data_hi - data_lo) * 0.03 or data_hi * 0.001
    hi_ax, lo_ax = data_hi + pad, data_lo - pad

    def py(p):
        return TOP + (hi_ax - p) / (hi_ax - lo_ax) * (BOT - TOP)

    def text_w(s, f):
        return d.textlength(s, font=f)

    last = rows[-1][4]
    tags = [
        (vp["poc_price"], "POC", _GOLD),
        (vp["vah"], "VAH", _BLUE),
        (vp["val"], "VAL", _BLUE),
        (last, "LAST", _UP if rows[-1][4] >= rows[-1][1] else _DOWN),
    ]
    tag_ys = [py(p) for p, _, _ in tags]

    # grid + price labels (skip labels that would collide with a tag)
    for i in range(6):
        p = lo_ax + (hi_ax - lo_ax) * i / 5
        y = py(p)
        d.line([(L, y), (prof_r, y)], fill=_GRID, width=1)
        if all(abs(y - ty) > 24 for ty in tag_ys):
            d.text((prof_r + 8, y - 8), _fmt_price(p), fill=_TXT, font=f_sm)

    # candles
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

    # profile histogram (buy green + sell red; dimmed outside value area)
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

    # POC / VAH / VAL lines across candles + profile
    d.line([(L, py(vp["poc_price"])), (prof_r, py(vp["poc_price"]))], fill=_GOLD, width=2)
    _dashed(d, L, prof_r, py(vp["vah"]), _BLUE)
    _dashed(d, L, prof_r, py(vp["val"]), _BLUE)

    # price tags on the right
    for p, label, col in tags:
        y = py(p)
        s = f"{label} {_fmt_price(p)}"
        d.rectangle([prof_r + 4, y - 11, prof_r + 4 + text_w(s, f_sm) + 12, y + 11], fill=col)
        d.text((prof_r + 10, y - 8), s, fill=(0, 0, 0), font=f_sm)

    # x labels (candle area) + panel label
    fmt = "%d %b" if timeframe in ("1d", "1M") else "%d %b %H:%M"
    step = max(1, n // 6)
    for i in range(0, n, step):
        x = L + (i + 0.5) * cw
        lbl = datetime.fromtimestamp(rows[i][0] / 1000, tz=timezone.utc).strftime(fmt)
        d.text((max(4, x - text_w(lbl, f_sm) / 2), BOT + 10), lbl, fill=_TXT, font=f_sm)
    d.text((prof_l, BOT + 10), "Volume Profile", fill=_TXT, font=f_sm)

    # title + legend
    d.text((L, 20), f"{ticker}/USDT  ·  Volume Profile  ·  {timeframe}", fill=(255, 255, 255), font=f_title)
    d.text((L, 58), f"{n} candles  ·  Value Area {int(_VP_VALUE_AREA * 100)}%", fill=_TXT, font=f_sm)
    lx = prof_l
    d.rectangle([lx, 66, lx + 12, 78], fill=_UP)
    d.text((lx + 18, 63), "Buy", fill=_TXT, font=f_sm)
    d.rectangle([lx + 70, 66, lx + 82, 78], fill=_DOWN)
    d.text((lx + 88, 63), "Sell", fill=_TXT, font=f_sm)

    d.text((L, H - 34), "powered by - BELUGA.PY", fill=(150, 150, 150), font=f_sm)

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
    """Call once from inside the running event loop (e.g. right after `await init_exchange_async()`)."""
    global _warm_task
    if _warm_task is None or _warm_task.done():
        _warm_task = asyncio.create_task(vp_warmup_loop())
        logger.info("[vp_warmup] background task started")


def _vp_caption(e) -> str:
    upd = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%H:%M")
    return (f"📊 *Volume Profile* · `{e['ticker']}/USDT` · `{e['tf']}`\n"
            f"🟡 POC: `{_fmt_price(e['poc'])}`\n"
            f"🔵 VAH: `{_fmt_price(e['vah'])}`  |  VAL: `{_fmt_price(e['val'])}`\n"
            f"🕒 {e['n']} candles · {e['source']} · {upd} UTC\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
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
            png, source = await _make_chart(ticker, tf)
            caption = _chart_caption(ticker, tf, source)
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
