import asyncio
import io
import re
import time
import traceback
from datetime import datetime, timezone

import ccxt
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import texts as T
from config import logger
from state import bot_status, cache_movers
from utils import safe_react


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
# CHART — drawn with Pillow only (no matplotlib / mplfinance / pandas)
# Requires: pillow  (add to requirements.txt)
# =====================================================================
_TF_MAPPING = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h",
    "1d": "1d", "1D": "1d", "1m": "1M", "1M": "1M",
}
_UP = (0, 196, 140)
_DOWN = (255, 51, 102)
_BG = (13, 13, 13)
_GRID = (34, 34, 34)
_TXT = (204, 204, 204)


def _fetch_ohlcv_sync(ticker: str, timeframe: str, limit: int = 60):
    """Returns (rows, source). rows = [[ts_ms, o, h, l, c, v], ...]. Binance REST first, ccxt fallback."""
    symbol = f"{ticker}USDT"
    for host in _BINANCE_HOSTS:
        try:
            r = requests.get(f"{host}/api/v3/klines",
                             params={"symbol": symbol, "interval": timeframe, "limit": limit},
                             headers=_HTTP_UA, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data:
                    rows = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in data]
                    return rows, "Binance"
        except Exception:
            continue

    if exchange is not None:
        try:
            raw = exchange.fetch_ohlcv(f"{ticker}/USDT", timeframe, limit=limit)
            rows = []
            for k in raw or []:
                if None in k[:6]:
                    continue
                rows.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
            if rows:
                return rows, str(getattr(exchange, "name", "Exchange"))
        except Exception as e:
            logger.warning(f"[chart] ccxt ohlcv failed: {str(e)[:80]}")
    return None, ""


def _render_chart_sync(rows, ticker: str, timeframe: str) -> bytes:
    """Candles + volume drawn straight onto a PIL image."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            return ImageFont.load_default()

    rows = sorted(rows, key=lambda r: r[0])
    n = len(rows)

    W, H = 1200, 760
    L, R, TOP = 20, 110, 70
    price_bottom = 530
    vol_top, vol_bottom = 550, 690
    plot_w = W - L - R

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_sm, f_title = font(15), font(24)

    max_h = max(r[2] for r in rows)
    min_l = min(r[3] for r in rows)
    span = (max_h - min_l) or (max_h * 0.01) or 1e-9
    hi, lo = max_h + span * 0.05, min_l - span * 0.05
    max_v = max(r[5] for r in rows) or 1.0

    def py(p):
        return TOP + (hi - p) / (hi - lo) * (price_bottom - TOP)

    def text_w(s, f):
        return d.textlength(s, font=f)

    # horizontal grid + price labels
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

    # x labels
    fmt = "%d %b" if timeframe in ("1d", "1M") else "%d %b %H:%M"
    step = max(1, n // 6)
    for i in range(0, n, step):
        x = L + (i + 0.5) * cw
        lbl = datetime.fromtimestamp(rows[i][0] / 1000, tz=timezone.utc).strftime(fmt)
        d.text((x - text_w(lbl, f_sm) / 2, vol_bottom + 10), lbl, fill=_TXT, font=f_sm)

    # last price marker + title
    last = rows[-1][4]
    ly = py(last)
    lc = _UP if rows[-1][4] >= rows[-1][1] else _DOWN
    d.line([(L, ly), (W - R, ly)], fill=lc, width=1)
    tag = _fmt_price(last)
    d.rectangle([W - R + 2, ly - 11, W - R + 8 + text_w(tag, f_sm) + 6, ly + 11], fill=lc)
    d.text((W - R + 8, ly - 8), tag, fill=(0, 0, 0), font=f_sm)
    d.text((L, 20), f"{ticker}/USDT  ·  {timeframe}  ·  {tag}", fill=(255, 255, 255), font=f_title)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _clean_ticker(raw: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    return t[:15] or "BTC"


async def _send_chart(message, ticker: str, timeframe: str):
    """Shared by /chart command and the Refresh button. `message` = any telegram Message."""
    sm = await message.reply_text(
        f"⚡ Generating chart for *{ticker}/USDT* (`{timeframe}`)...", parse_mode=ParseMode.MARKDOWN
    )
    loop = asyncio.get_running_loop()
    try:
        rows, source = await loop.run_in_executor(None, lambda: _fetch_ohlcv_sync(ticker, timeframe))
        if not rows:
            raise ValueError(f"No candle data for {ticker}/USDT")

        png = await loop.run_in_executor(None, lambda: _render_chart_sync(rows, ticker, timeframe))
        bio = io.BytesIO(png)
        bio.name = f"{ticker}_{timeframe}.png"

        try:
            await sm.delete()
        except Exception:
            pass

        keyboard = [
            [
                InlineKeyboardButton("📊 Indicators (RSI/MA)", callback_data=f"ind_{ticker}_{timeframe}"),
                InlineKeyboardButton("🗺 Volume Footprint", callback_data=f"fp_{ticker}"),
            ],
            [
                InlineKeyboardButton("📚 Orderbook Depth", callback_data=f"ob_{ticker}"),
                InlineKeyboardButton("🔄 Refresh Chart", callback_data=f"chart_{ticker}_{timeframe}"),
            ],
        ]
        caption = (f"⚡ *Live Chart* : `{ticker}/USDT`\n"
                   f"⏱ Timeframe: `{timeframe}`\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🐾 _via Beluga Quant Engine · {source}_")
        await message.reply_photo(photo=bio, caption=caption, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=InlineKeyboardMarkup(keyboard))
    except BaseException as e:  # RecursionError included
        tail = " || ".join(traceback.format_exc().splitlines()[-10:])
        logger.error(f"[crypto_chart] {ticker} {timeframe}: {type(e).__name__}: {str(e)[:100]} | TB: {tail}")
        bot_status["error_count"] += 1
        try:
            await sm.edit_text(f"❌ Chart error for {ticker}/USDT: {type(e).__name__}: {str(e)[:150]}")
        except Exception:
            pass
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise


async def crypto_chart_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handles /chart command with timeframes: 5m, 15m, 1h, 4h, 1d/1D, 1M."""
    if not u.message:
        return
    try:
        parts = (u.message.text or "").split()
        ticker = _clean_ticker(parts[1]) if len(parts) >= 2 else "BTC"
        timeframe = "1h"
        for part in parts[2:]:
            if part in _TF_MAPPING:
                timeframe = _TF_MAPPING[part]
                break
            if part.lower() in _TF_MAPPING:
                timeframe = _TF_MAPPING[part.lower()]
                break

        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "⚡")
        await _send_chart(u.message, ticker, timeframe)
    except Exception as e:
        logger.error(f"[crypto_chart] {e}")


# --- ORDERBOOK & FOOTPRINT HELPER FUNCTIONS ---
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

        text += f"\n━━━━━━━━━━━━━━━━━━━━\n🐾 _{source} Orderbook Feed_"
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
                f"━━━━━━━━━━━━━━━━━━━━\n🐾 _Derived from Binance AggTrades_")
        return text
    except Exception as e:
        return f"❌ Error computing footprint: {str(e)[:150]}"


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles clicks on Inline Keyboard buttons for Orderbook, Footprint, Indicators, and Chart Refresh."""
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    data = query.data or ""

    try:
        if data.startswith("ob_"):
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

        elif data.startswith("chart_"):
            _, ticker, tf = data.split("_", 2)
            await _send_chart(query.message, _clean_ticker(ticker), tf if tf in _TF_MAPPING.values() else "1h")
    except Exception as e:
        logger.error(f"[button_callback] {e}")
