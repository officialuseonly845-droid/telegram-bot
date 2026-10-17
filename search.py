"""/search, /ss and /bananalogic.

Retrieval lives in search_engine.py (multi-provider text + image search with
automatic fallback). This file only orchestrates: gather facts, ask the AI to
condense them, find a working image, and send everything back safely."""
import asyncio
import io
import re
import urllib.parse
from typing import Optional
import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
import texts as T
from ai_models import ai
from config import WIKI_UA, logger
from search_engine import fetch_image_bytes, image_search, text_search
from texts import BANANA_PROMPT
from utils import safe_react, send_photo_safe

_EMPTY_WIKI = {"found": False, "title": "", "url": "", "intro": "", "sections": [], "image": None}
_EMPTY_WEB = {"found": False, "ai_answer": "", "snippets": [], "results": [], "source": ""}


def wiki_summary(query):
    """
    Uses Wikipedia's OFFICIAL REST v1 API (https://www.mediawiki.org/wiki/API:REST_API)
    instead of scraping the legacy action=query endpoint. Two calls:
      1. /search/page  -> find the best-matching title for the query
      2. /page/{title}/summary -> official clean summary + thumbnail image
    Return shape kept identical to before so every caller (search_handler,
    bananalogic_handler, wiki_page_image) keeps working unchanged.
    """
    out = {"found": False, "title": "", "url": "", "intro": "", "sections": [], "image": None}
    try:
        sr = requests.get(
            "https://en.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": query, "limit": 1},
            headers=WIKI_UA, timeout=10,
        )
        if sr.status_code != 200:
            return out
        hits = sr.json().get("pages", [])
        if not hits:
            return out
        best_title = hits[0]["title"]
        best_key = hits[0].get("key", best_title.replace(" ", "_"))

        summary_r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(best_key)}",
            headers=WIKI_UA, timeout=10,
        )
        if summary_r.status_code != 200:
            return out
        data = summary_r.json()
        intro = data.get("extract", "").strip()
        if not intro:
            return out
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page") \
            or f"https://en.wikipedia.org/wiki/{urllib.parse.quote(best_key)}"
        thumb = data.get("thumbnail", {}).get("source") or data.get("originalimage", {}).get("source")

        out.update({
            "found": True, "title": best_title, "url": page_url,
            "intro": intro[:1500], "sections": [], "image": thumb,
        })
    except Exception:
        pass
    return out


def wiki_page_image(title: str) -> Optional[str]:
    """
    Fetch the main thumbnail image URL for a Wikipedia page via the raw
    MediaWiki `pageimages` prop (wikipediaapi itself doesn't expose this).
    Returns None if the page has no image or the request fails.
    """
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "titles": title, "prop": "pageimages",
                "piprop": "original", "format": "json",
            },
            headers=WIKI_UA, timeout=10,
        )
        pages = r.json().get("query", {}).get("pages", {})
        for _pid, page in pages.items():
            original = page.get("original", {})
            if original.get("source"):
                return original["source"]
    except Exception:
        pass
    return None


def _is_url(text: str) -> bool:
    return bool(re.match(r"^https?://\S+$", text.strip(), re.IGNORECASE))


async def _screenshot_website(u: Update, c: ContextTypes.DEFAULT_TYPE, url: str):
    """
    /search <website link> — sends a screenshot of the site instead of
    running a text search. Uses WordPress's free mShots screenshot service
    (no API key needed). Crash-proof: falls back to a plain message with
    the link if the screenshot can't be generated or sent.
    """
    cid = u.effective_chat.id
    sm = await u.message.reply_text(T.SCREENSHOT_WEBSITE_MSG_1, parse_mode=ParseMode.MARKDOWN)
    shot_url = f"https://s.wordpress.com/mshots/v1/{urllib.parse.quote(url, safe='')}?w=1280&h=720"
    caption = f"📸 *Screenshot of:*\n{url}"
    try:
        await sm.delete()
    except Exception:
        pass
    sent = await send_photo_safe(c.bot, cid, shot_url, caption=caption, parse_mode=ParseMode.MARKDOWN)
    if not sent:
        try:
            await u.message.reply_text(T.screenshot_website_msg_2(url), reply_to_message_id=u.message.message_id)
        except Exception as e:
            logger.error(f"[_screenshot_website] {e}")

def _md_escape(t: str) -> str:
    """Escapes legacy-Markdown control characters so user text can't break formatting."""
    return re.sub(r"([_*`\[])", r"\\\1", t or "")


def _plain(t: str) -> str:
    """Markdown -> plain text, used when Telegram rejects our formatting."""
    t = re.sub(r"\\([_*`\[])", r"\1", t)
    return re.sub(r"[*`]", "", t)


async def _reply(u: Update, text: str):
    """reply_text that never dies on a Markdown parse error (AI output often has stray * or _)."""
    try:
        return await u.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_to_message_id=u.message.message_id)
    except BadRequest as e:
        if "parse" not in str(e).lower() and "entit" not in str(e).lower():
            raise
        return await u.message.reply_text(_plain(text), reply_to_message_id=u.message.message_id)


async def _send_photo_bytes(c: ContextTypes.DEFAULT_TYPE, cid: int, data: bytes, caption: str) -> bool:
    async def attempt(cap, pm):
        bio = io.BytesIO(data)
        bio.name = "image.jpg"
        await c.bot.send_photo(chat_id=cid, photo=bio, caption=cap, parse_mode=pm)
    try:
        await attempt(caption, ParseMode.MARKDOWN)
        return True
    except BadRequest as e:
        if "parse" in str(e).lower() or "entit" in str(e).lower():
            try:
                await attempt(_plain(caption), None)
                return True
            except Exception as e2:
                logger.warning(f"[search] photo resend failed: {e2}")
        else:
            logger.warning(f"[search] send_photo failed: {e}")
    except Exception as e:
        logger.warning(f"[search] send_photo failed: {e}")
    return False


async def _deliver(u: Update, c: ContextTypes.DEFAULT_TYPE, caption: str, image: Optional[bytes]):
    """Photo + caption when possible (long text goes in a follow-up, captions cap at 1024), else text only."""
    cid = u.effective_chat.id
    if image:
        if len(caption) <= 1000:
            if await _send_photo_bytes(c, cid, image, caption):
                return
        else:
            head = caption.split("\n", 1)[0][:200]
            if await _send_photo_bytes(c, cid, image, head):
                await _reply(u, caption[:3800])
                return
    try:
        await _reply(u, caption[:3800])
    except Exception as e:
        logger.error(f"[search] final text send failed: {e}")


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) >= 3}


def _wiki_matches(query: str, wiki: dict) -> bool:
    """Only trust the Wikipedia picture when the article title actually relates to the query."""
    return bool(wiki.get("found")) and bool(_words(query) & _words(wiki.get("title", "")))


async def _first_image(urls: list) -> Optional[bytes]:
    """Downloads up to 5 candidates in parallel and returns the best-ranked one that works."""
    urls = [u for u in urls if u][:5]
    if not urls:
        return None
    loop = asyncio.get_running_loop()
    results = await asyncio.gather(*[loop.run_in_executor(None, fetch_image_bytes, u) for u in urls],
                                   return_exceptions=True)
    for r in results:
        if isinstance(r, (bytes, bytearray)) and r:
            return bytes(r)
    return None


async def _gather_sources(query: str):
    """Wikipedia + web text + image candidates, fetched in parallel. Never raises."""
    loop = asyncio.get_running_loop()
    wiki, web, imgs = await asyncio.gather(
        loop.run_in_executor(None, wiki_summary, query),
        loop.run_in_executor(None, text_search, query),
        loop.run_in_executor(None, image_search, query, 8),
        return_exceptions=True,
    )
    wiki = wiki if isinstance(wiki, dict) else dict(_EMPTY_WIKI)
    web = web if isinstance(web, dict) else dict(_EMPTY_WEB)
    imgs = imgs if isinstance(imgs, list) else []
    return wiki, web, imgs


async def _image_for(query: str, wiki: dict, web_images: list) -> Optional[bytes]:
    cands = []
    if _wiki_matches(query, wiki):
        wimg = wiki.get("image")
        if not wimg and wiki.get("title"):
            try:
                wimg = await asyncio.get_running_loop().run_in_executor(None, wiki_page_image, wiki["title"])
            except Exception:
                wimg = None
        if wimg:
            cands.append(wimg)
    return await _first_image(cands + list(web_images))


async def web_summarise(query, wiki, web, system_prompt, max_tok=500):
    """Condenses whatever facts were found. Returns "" when there is nothing to condense."""
    ctx = []
    if web.get("results"):
        lines = [f"{i}. {r['title']}: {r['snippet']}" if r.get("snippet") else f"{i}. {r['title']}"
                 for i, r in enumerate(web["results"][:6], 1)]
        ctx.append("Web results:\n" + "\n".join(lines))
    if wiki.get("found"):
        ctx.append(f"Wikipedia ({wiki['title']}):\n{wiki['intro']}")
    if not ctx:
        return ""
    return await ai(system_prompt, T.web_summarise_usermsg_1(query, ctx), "", max_tok=max_tok)


async def _run_search_and_reply(u: Update, c: ContextTypes.DEFAULT_TYPE, query: str):
    """Core search logic shared by /search and the bare 'search X' / 'google X' keyword trigger."""
    cid = u.effective_chat.id

    if _is_url(query):
        await _screenshot_website(u, c, query)
        return

    await safe_react(c.bot, cid, u.message.message_id, "🔍")
    sm = await u.message.reply_text(T.RUN_SEARCH_AND_REPLY_MSG_1, parse_mode=ParseMode.MARKDOWN)

    wiki, web, web_images = await _gather_sources(query)
    summary = await web_summarise(query, wiki, web, T.SEARCH_PROMPT_1, max_tok=220)
    if not summary and wiki.get("intro"):
        summary = wiki["intro"][:600]
    if not summary and web.get("results"):
        summary = web["results"][0].get("snippet") or web["results"][0]["title"]

    if not summary:
        try:
            await sm.edit_text(T.RUN_SEARCH_AND_REPLY_MSG_2)
        except Exception:
            pass
        return

    image = await _image_for(query, wiki, web_images)
    try:
        await sm.delete()
    except Exception:
        pass

    link = (web["results"][0]["url"] if web.get("results") else "") or (wiki.get("url") if wiki.get("found") else "")
    caption = f"🔍 *{_md_escape(query)}*\n\n{summary}"
    if link:
        caption += f"\n\n🔗 {_md_escape(link)}"
    await _deliver(u, c, caption, image)


async def ss_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/ss <query> — sends the most relevant web image for a query. For a website screenshot, use /search <url> instead."""
    if not u.message:
        return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await u.message.reply_text(T.SS_MSG_1, parse_mode=ParseMode.MARKDOWN)
        return
    query = parts[1].strip()
    cid = u.effective_chat.id
    sm = await u.message.reply_text(T.SS_MSG_2, parse_mode=ParseMode.MARKDOWN)
    loop = asyncio.get_running_loop()
    try:
        urls = await loop.run_in_executor(None, image_search, query, 8)
    except Exception:
        urls = []
    image = await _first_image(urls)
    try:
        await sm.delete()
    except Exception:
        pass
    if not image:
        await u.message.reply_text(T.SS_MSG_3)
        return
    if not await _send_photo_bytes(c, cid, image, T.ss_msg_4(query)):
        await u.message.reply_text(T.ss_msg_5(query))


async def search_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /search <query> — Wikipedia + live web results condensed into a 60-100 word
    AI summary with a relevant image and a source link.
    /search <website link> — sends a screenshot of that site instead.
    """
    if not u.message:
        return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await u.message.reply_text(T.SEARCH_MSG_1, parse_mode=ParseMode.MARKDOWN)
        return
    await _run_search_and_reply(u, c, parts[1].strip())


async def bananalogic_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """/bananalogic <question> — short conversational AI answer + image.
    Uses live web facts when available, and falls back to the AI's own knowledge
    when search comes back empty (so it still answers instead of erroring)."""
    if not u.message:
        return
    parts = u.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await u.message.reply_text(T.BANANALOGIC_MSG_1)
        return
    query = parts[1].strip()
    cid = u.effective_chat.id
    await safe_react(c.bot, cid, u.message.message_id, "🍌")
    sm = await u.message.reply_text(T.BANANALOGIC_MSG_2, parse_mode=ParseMode.MARKDOWN)

    wiki, web, web_images = await _gather_sources(query)
    answer = await web_summarise(query, wiki, web, BANANA_PROMPT, max_tok=450)
    if not answer:
        answer = await ai(T.BANANA_PROMPT_NOCTX, query, "", max_tok=450)
    if not answer:
        try:
            await sm.edit_text(T.BANANALOGIC_MSG_3)
        except Exception:
            pass
        return

    image = await _image_for(query, wiki, web_images)
    try:
        await sm.delete()
    except Exception:
        pass
    await _deliver(u, c, f"❝ *{_md_escape(query)}* ❞\n\n{answer.strip()}\n\n🐾 _via BananaLogic_", image)
