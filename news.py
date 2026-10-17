"""/news, /ainews, /technews."""
import asyncio
import random
import re
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import texts as T
from config import G_HDR, logger
from state import bot_status, news_cache
from styles import _format_news_body
from utils import clean_html, safe_react


def fetch_google_news(feed_type: str) -> list:
    feeds = {
        "crypto": "https://news.google.com/rss/search?q=cryptocurrency+bitcoin",
        "ai": "https://news.google.com/rss/search?q=artificial+intelligence+AI",
        "tech": "https://news.google.com/rss/search?q=technology+innovation"
    }
    url = feeds.get(feed_type, feeds["tech"])
    results = []
    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:20]:
            title = entry.get("title", "").strip()
            title = re.sub(r'\s*-\s*[^-]{3,40}$', '', title).strip()
            link = entry.get("link", "#")
            pub_date = entry.get("published", "")[:16]
            summary_html = entry.get("summary", "")
            img_url = None
            img_match = re.search(r'src=["\'](https://[^"\']+\.(?:jpg|jpeg|png|webp|gif))["\']', summary_html, re.IGNORECASE)
            if img_match:
                img_url = img_match.group(1)
            full_text = ""
            try:
                r = requests.get(link, headers=G_HDR, timeout=8)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    for tag in soup(["script","style","nav","footer","header","aside"]):
                        tag.decompose()
                    paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 50]
                    full_text = " ".join(paragraphs[:8])[:1800]
                    if not img_url:
                        og_img = soup.find("meta", property="og:image")
                        if og_img and og_img.get("content"):
                            img_url = og_img["content"]
            except Exception:
                pass
            summary_text = clean_html(summary_html)[:200] if not full_text else full_text[:1800]
            if title:
                results.append({"title": title[:200], "link": link, "summary": summary_text, "image": img_url, "date": pub_date})
    except Exception as e:
        logger.error(f"[fetch_google_news] {e}")
    return results


async def execute_news_flow(u: Update, c: ContextTypes.DEFAULT_TYPE, feed_type: str, label: str):
    if not u.message:
        return
    try:
        cid = u.effective_chat.id
        await safe_react(c.bot, cid, u.message.message_id, "📰")
        sm = await u.message.reply_text(T.execute_news_flow_msg_1(label), parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        now = time.time()
        if news_cache[feed_type]["ts"] and (now - news_cache[feed_type]["ts"]) < 300:
            items = news_cache[feed_type]["data"]
        else:
            items = await loop.run_in_executor(None, fetch_google_news, feed_type)
            news_cache[feed_type]["ts"] = now
            news_cache[feed_type]["data"] = items
        if not items:
            await sm.edit_text(T.EXECUTE_NEWS_FLOW_MSG_2)
            return
        await sm.delete()
        top = random.choice(items[:min(8, len(items))])
        body = _format_news_body(top["summary"])
        caption = (f"📰 *{label.upper()}*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"📌 *{top['title']}*\n\n{body}\n\n━━━━━━━━━━━━━━━━━━━━\n📅 {top['date']}")
        if len(caption) > 1020:
            caption = caption[:1017] + "..."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📖 Read Full Article", url=top["link"], style="primary")]])
        sent = False
        if top["image"]:
            try:
                await u.message.reply_photo(photo=top["image"], caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb, reply_to_message_id=u.message.message_id)
                sent = True
            except Exception:
                logger.warning("[news_img] image send failed, falling back to text")
        if not sent:
            await u.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb, reply_to_message_id=u.message.message_id)
        bot_status["message_count"] += 1
    except Exception as e:
        logger.error(f"[execute_news_flow] {e}")
