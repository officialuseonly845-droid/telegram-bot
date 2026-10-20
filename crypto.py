import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import asyncio
import time
import ccxt
import requests
import io
import mplfinance as mpf
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import logger
from state import bot_status, cache_movers
from utils import safe_react


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
    hosts = ("https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com")
    
    for host in hosts:
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
    errors = []
    # 1) Binance
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
    """Handles /chart command with support for timeframes: 5m, 15m, 1h, 4h, 1d/1D, 1M."""
    if not u.message or not exchange:
        return
    try:
        parts = u.message.text.split()
        ticker = "BTC"
        timeframe = "1h"  # default timeframe
        
        # Parse ticker if specified (e.g., /chart eth)
        if len(parts) >= 2:
            ticker = parts[1].upper()
            
        # Mapping for timeframes: 5m, 15m, 1h, 4h, 1d/1D, 1M
        tf_mapping = {
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
            "1D": "1d",
            "1m": "1M",
            "1M": "1M"
        }
        
        # Check parts for timeframe match
        for part in parts[2:]:
            if part in tf_mapping:
                timeframe = tf_mapping[part]
                break
            elif part.lower() in tf_mapping:
                timeframe = tf_mapping[part.lower()]
                break

        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "📈")
        sm = await u.message.reply_text(f"⚡ Generating Binance chart for *{ticker}/USDT* (`{timeframe}`)...", parse_mode=ParseMode.MARKDOWN)
        
        loop = asyncio.get_running_loop()
        try:
            ohlcv = await loop.run_in_executor(None, lambda: exchange.fetch_ohlcv(f"{ticker}/USDT", timeframe, limit=50))
            if not ohlcv:
                raise ValueError("Empty dataset from Binance")
                
            df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
            
            # Ensure all columns are numeric floats and drop any NaNs
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df.dropna(inplace=True)
            
            df.set_index('Timestamp', inplace=True)
            
            # Remove duplicate timestamps & sort index to prevent mplfinance recursion errors
            df = df[~df.index.duplicated(keep='last')].sort_index()
            
            if df.empty:
                raise ValueError("Cleaned dataset is empty")

            buf = io.BytesIO()
            mc = mpf.make_marketcolors(up='#00C48C', down='#ff3366', inherit=True)
            s = mpf.make_mpf_style(base_mpf_style='charles', marketcolors=mc, gridcolor='#222222', facecolor='#0d0d0d')
            
            def _plot_and_save():
                fig, axes = mpf.plot(
                    df, type='candle', style=s, volume=True,
                    returnfig=True, figratio=(14, 9), dpi=115
                )
                fig.savefig(buf, bbox_inches='tight', dpi=115)
                plt.close(fig) # Prevent memory buildup and recursion errors
                
            await loop.run_in_executor(None, _plot_and_save)
            buf.seek(0)
            await sm.delete()

            # Inline Keyboard for Footprint, Orderbook, and Indicators
            keyboard = [
                [
                    InlineKeyboardButton("📊 Indicators (RSI/MA)", callback_data=f"ind_{ticker}_{timeframe}"),
                    InlineKeyboardButton("🗺 Volume Footprint", callback_data=f"fp_{ticker}")
                ],
                [
                    InlineKeyboardButton("📚 Orderbook Depth", callback_data=f"ob_{ticker}"),
                    InlineKeyboardButton("🔄 Refresh Chart", callback_data=f"chart_{ticker}_{timeframe}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            caption = (f"⚡ *Binance Live Chart* : `{ticker}/USDT`\n"
                       f"⏱ Timeframe: `{timeframe}`\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🐾 _via Beluga Quant Engine_")

            await u.message.reply_photo(photo=buf, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            
        except Exception as e:
            await sm.edit_text(f"❌ Error generating chart for `{ticker}/USDT`: `{str(e)}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"[crypto_chart] {e}")


# --- ORDERBOOK & FOOTPRINT HELPER FUNCTIONS ---
async def fetch_orderbook_data(ticker: str):
    """Fetches live orderbook (bids and asks) from Binance via CCXT."""
    try:
        loop = asyncio.get_running_loop()
        orderbook = await loop.run_in_executor(None, lambda: exchange.fetch_order_book(f"{ticker}/USDT", limit=10))
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        
        text = f"📚 *LIVE ORDERBOOK: {ticker}/USDT*\n━━━━━━━━━━━━━━━━━━━━\n\n🔴 *ASKS (Sell Orders)*\n"
        for price, amt in reversed(asks[:5]):
            text += f"`{price:,.2f}  |  {amt:,.4f}`\n"
            
        text += "\n🟢 *BIDS (Buy Orders)*\n"
        for price, amt in bids[:5]:
            text += f"`{price:,.2f}  |  {amt:,.4f}`\n"
            
        text += "\n━━━━━━━━━━━━━━━━━━━━\n🐾 _Binance Orderbook Feed_"
        return text
    except Exception as e:
        return f"❌ Could not fetch orderbook for {ticker}: {e}"


async def fetch_footprint_summary(ticker: str):
    """Calculates basic volume footprint delta / recent aggressive buys/sells from Binance aggTrades."""
    try:
        url = f"https://api.binance.com/api/v3/aggTrades?symbol={ticker}USDT&limit=50"
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return f"❌ Footprint data unavailable for {ticker}"
        
        trades = r.json()
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
        return f"❌ Error computing footprint: {e}"


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles clicks on Inline Keyboard buttons for Orderbook, Footprint, Indicators, and Chart Refresh."""
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("ob_"):
        ticker = data.split("_")[1]
        ob_text = await fetch_orderbook_data(ticker)
        await query.message.reply_text(ob_text, parse_mode=ParseMode.MARKDOWN)
        
    elif data.startswith("fp_"):
        ticker = data.split("_")[1]
        fp_text = await fetch_footprint_summary(ticker)
        await query.message.reply_text(fp_text, parse_mode=ParseMode.MARKDOWN)
        
    elif data.startswith("ind_"):
        _, ticker, tf = data.split("_")
        await query.message.reply_text(f"📊 Indicator Options for *{ticker}* (`{tf}`):\n• RSI (Relative Strength Index)\n• Moving Averages (EMA/SMA)\n• Bollinger Bands\n\n*(Custom indicator toggles enabled via Beluga Engine)*", parse_mode=ParseMode.MARKDOWN)
        
    elif data.startswith("chart_"):
        _, ticker, tf = data.split("_")
        # Simulate /chart refresh safely
        query.message.text = f"/chart {ticker} {tf}"
        update.message = query.message
        await crypto_chart_handler(update, context)
