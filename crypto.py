import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import Rectangle
import asyncio
import io
import re
import time
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
        await safe_react(c.bot, cid, u.message.message_id, "💰")
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
# CHART (pure matplotlib — no mplfinance / pandas, so no recursion errors)
# =====================================================================
_TF_MAPPING = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h",
    "1d": "1d", "1D": "1d", "1m": "1M", "1M": "1M",
}
_UP = "#00C48C"
_DOWN = "#ff3366"
_BG = "#0d0d0d"
_GRID = "#222222"
_TXT = "#cccccc"


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
    """Draws candles + volume with a standalone Figure (no pyplot global state, thread-safe)."""
    rows = sorted(rows, key=lambda r: r[0])
    n = len(rows)

    fig = Figure(figsize=(10, 6.4), dpi=115, facecolor=_BG)
    FigureCanvasAgg(fig)
    gs = fig.add_gridspec(2, 1, height_ratios=[4, 1], hspace=0.04)
    ax = fig.add_subplot(gs[0])
    axv = fig.add_subplot(gs[1], sharex=ax)

    for a in (ax, axv):
        a.set_facecolor(_BG)
        a.grid(True, color=_GRID, linewidth=0.6)
        a.tick_params(colors=_TXT, labelsize=8)
        a.yaxis.tick_right()
        for sp in a.spines.values():
            sp.set_color(_GRID)

    lows = [r[3] for r in rows]
    highs = [r[2] for r in rows]
    min_l, max_h = min(lows), max(highs)
    span = (max_h - min_l) or max_h * 0.01 or 1e-9

    for i, (_, o, h, l, c, v) in enumerate(rows):
        col = _UP if c >= o else _DOWN
        ax.vlines(i, l, h, color=col, linewidth=1)
        body_low = min(o, c)
        body_h = max(abs(c - o), span * 0.002)
        ax.add_patch(Rectangle((i - 0.35, body_low), 0.7, body_h, facecolor=col, edgecolor=col, linewidth=0.5))
        axv.bar(i, v, width=0.7, color=col)

    ax.set_xlim(-1, n)
    ax.set_ylim(min_l - span * 0.05, max_h + span * 0.05)
    ax.tick_params(labelbottom=False)

    fmt = "%d %b" if timeframe in ("1d", "1M") else "%d %b %H:%M"
    step = max(1, n // 6)
    idx = list(range(0, n, step))
    axv.set_xticks(idx)
    axv.set_xticklabels(
        [datetime.fromtimestamp(rows[i][0] / 1000, tz=timezone.utc).strftime(fmt) for i in idx],
        rotation=0
    )
    axv.set_ylabel("Vol", color=_TXT, fontsize=8)

    last = rows[-1][4]
    fig.suptitle(f"{ticker}/USDT · {timeframe} · {_fmt_price(last)}", color="white", fontsize=12, y=0.95)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_BG, bbox_inches="tight")
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
    except Exception as e:
        logger.error(f"[crypto_chart] {ticker} {timeframe}: {e}")
        bot_status["error_count"] += 1
        try:
            await sm.edit_text(f"❌ Chart error for {ticker}/USDT: {str(e)[:200]}")
        except Exception:
            pass


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

        await safe_react(c.bot, u.effective_chat.id, u.message.message_id, "📈")
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
