"""/price, /topgainers, /toplosers, /chart commands."""
import matplotlib
matplotlib.use('Agg')
import asyncio
import time
import ccxt
import requests
import io
import mplfinance as mpf
import pandas as pd
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import logger
from state import bot_status, cache_movers
from utils import safe_react


def get_exchange(prefer: str = "bybit"):
    """
    Synchronous, blocking network call (ccxt.load_markets()).
    NEVER call this at module import time — it must only run inside the
    event loop via init_exchange_async() so it can't delay HTTP port
    binding or the Telegram polling startup. Render kills deploys that
    don't open a port quickly, which is what caused 'Application exited
    early' here: get_exchange() used to run at import time and blocked
    everything else from starting.
    """
    exchanges = ["bybit", "okx", "bitget", "kraken", "binance"]
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


async def crypto_price_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not exchange:
        return
    try:
        ticker = (c.args[0].upper() if c.args else "BTC")
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "💰")
        sm = await u.message.reply_text(T.crypto_price_msg_1(ticker), parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        try:
            td = await loop.run_in_executor(None, exchange.fetch_ticker, f"{ticker}/USDT")
            price = td.get('last', 0.0)
            change = td.get('percentage', 0.0)
            vol = td.get('baseVolume', 0.0)
            high = td.get('high', 0.0)
            low = td.get('low', 0.0)
            sign = "🟩 +" if change >= 0 else "🟥 "
            res = (f"⚡ *{ticker}/USDT*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"🏷 *Price*\n`{price:,.4f} USDT`\n\n"
                   f"📊 *24h Change*\n`{sign}{change:.2f}%`\n\n"
                   f"📈 *24h High*\n`{high:,.4f}`\n\n"
                   f"📉 *24h Low*\n`{low:,.4f}`\n\n"
                   f"🔄 *Volume*\n`{vol:,.2f} {ticker}`\n\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n🐾 _via Beluga Quant Engine_")
            await sm.edit_text(res, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await sm.edit_text(T.crypto_price_msg_2(e))
            bot_status["error_count"] += 1
    except Exception as e:
        logger.error(f"[crypto_price] {e}")


# Coins that would only pollute a "movers" list (stablecoins / fiat pairs) and the
# minimum 24h traded value (in USDT) a coin needs before it counts. Without the
# volume floor the list is dominated by illiquid junk pairs showing +300%.
_STABLE_BASES = {
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDD", "PYUSD", "USDE", "USD1", "RLUSD",
    "UST", "USTC", "EUR", "EURI", "AEUR", "GBP", "TRY", "BRL", "ARS", "UAH", "PLN", "RON", "CZK", "JPY",
}
_MIN_QUOTE_VOL = 3_000_000
_HTTP_UA = {"User-Agent": "Mozilla/5.0 (compatible; BelugaBot/12)"}


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
    """Blocking. Returns (records, source_name). Tries three independent sources so
    one outage / geo-block (Render IPs are often blocked by exchange APIs) can't
    break the command:
      1. Binance public market-data hosts (no key, not geo-blocked)
      2. CoinGecko top-250 by market cap
      3. the ccxt exchange the bot already connected to
    Each record: {"sym", "ch" (24h %), "price", "vol" (24h USDT)}."""
    errors = []

    # 1) Binance: every USDT pair in one call.
    for host in ("https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"):
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


async def crypto_chart_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not exchange:
        return
    try:
        parts = u.message.text.split()
        ticker, timeframe = "BTC", "1h"
        if len(parts) >= 2:
            ticker = parts[1].upper()
        for tf in ["5m", "15m", "1h", "4h", "1d"]:
            if tf in u.message.text.lower():
                timeframe = tf
                break
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "📈")
        sm = await u.message.reply_text(T.crypto_chart_msg_1(ticker, timeframe), parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        try:
            ohlcv = await loop.run_in_executor(None, lambda: exchange.fetch_ohlcv(f"{ticker}/USDT", timeframe, limit=45))
            if not ohlcv:
                raise ValueError("Empty dataset")
            df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
            df.set_index('Timestamp', inplace=True)
            buf = io.BytesIO()
            mc = mpf.make_marketcolors(up='#00C48C', down='#ff3366', inherit=True)
            s = mpf.make_mpf_style(base_mpf_style='charles', marketcolors=mc, gridcolor='#222222', facecolor='#0d0d0d')
            def _plot():
                mpf.plot(df, type='candle', style=s, volume=True, savefig=dict(fname=buf, dpi=115, bbox_inches='tight'), figratio=(14,9))
            await loop.run_in_executor(None, _plot)
            buf.seek(0)
            await sm.delete()
            await u.message.reply_photo(photo=buf, caption=T.crypto_chart_msg_2(ticker, timeframe), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await sm.edit_text(T.crypto_chart_msg_3(e))
    except Exception as e:
        logger.error(f"[crypto_chart] {e}")
