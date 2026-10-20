"""Small shared helpers (sentiment, safe reactions, photo sending, admin checks, scoring)."""
import asyncio
import random
import re
import hashlib
import io
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from telegram import ReactionTypeEmoji
from textblob import TextBlob
from typing import Optional
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from config import G_HDR, OWNER_ID, logger
from state import daily_stats, db, member_tags
from styles import SENTIMENT_NEGATIVE, SENTIMENT_NEUTRAL, SENTIMENT_POSITIVE


# --- Sentiment analysis (VADER — fast, pure-python, safe to run on every message) ---
vader_analyzer = SentimentIntensityAnalyzer()


def get_sentiment(text: str) -> dict:
    """Returns VADER's polarity scores dict: {'neg','neu','pos','compound'}.
    compound ranges -1 (very negative) to +1 (very positive)."""
    try:
        return vader_analyzer.polarity_scores(text or "")
    except Exception:
        return {"neg": 0.0, "neu": 1.0, "pos": 0.0, "compound": 0.0}


def mood_hint(text: str) -> str:
    """Turns VADER's sentiment score into a short instruction appended to
    the system prompt, so the AI's reply tone is refined to actually match
    how the user sounds (gentle for upset messages, matched energy for
    happy ones) instead of a flat one-size-fits-all tone."""
    score = get_sentiment(text).get("compound", 0.0)
    if score <= -0.6:
        return "\n\nThe user's message sounds quite upset or distressed — be extra gentle, warm and understanding in your reply, don't joke around."
    elif score <= -0.3:
        return "\n\nThe user's message sounds a bit annoyed or down — soften your tone and be supportive."
    elif score >= 0.6:
        return "\n\nThe user's message sounds very happy/excited — match that energy!"
    elif score >= 0.3:
        return "\n\nThe user's message sounds upbeat — keep your reply light and positive."
    return ""


def get_daily_stats(cid: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    d = daily_stats.get(cid)
    if not d or d["date"] != today:
        d = {"date": today, "messages": 0, "mutes": 0, "bans": 0, "user_counts": {}, "milestones_hit": set()}
        daily_stats[cid] = d
    return d


def tagged_name(cid: str, uid: str, name: str) -> str:
    """Prefixes a user's display name with their purchased shop tag, if any."""
    tag = member_tags.get(cid, {}).get(uid)
    return f"「{tag}」 {name}" if tag else name


async def _delete_after(bot, chat_id, msg_id, delay: int):
    """Shared helper: waits `delay` seconds then deletes a message, silently no-oping on failure."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


async def safe_react(bot, chat_id: int, msg_id: int, emoji: str = None):
    if not emoji:
        emoji = random.choice(["🐱","🐾","❤️","🔥","👍","😻","😼","😂","✨","👀"])
    try:
        await asyncio.wait_for(bot.set_message_reaction(chat_id=chat_id, message_id=msg_id, reaction=[ReactionTypeEmoji(emoji=emoji)]), timeout=5.0)
    except Exception:
        pass


def clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-zA-Z#0-9]+;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_resolved_image_cache: dict = {}


def resolve_postimg_direct_url(page_url: str) -> Optional[str]:
    """
    postimg.cc share links (e.g. postimg.cc/HrV5wnj0) are HTML viewer pages,
    not direct image files — Telegram's send_photo needs a direct file URL.
    This scrapes the page's og:image meta tag to get the real direct link.
    Cached in-process so we only ever hit postimg.cc once per URL, and any
    failure just returns None so callers can fall back to text-only sends
    instead of crashing.
    """
    if page_url in _resolved_image_cache:
        return _resolved_image_cache[page_url]
    try:
        r = requests.get(page_url, headers=G_HDR, timeout=8)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                direct_url = og_img["content"]
                _resolved_image_cache[page_url] = direct_url
                return direct_url
    except Exception as e:
        logger.warning(f"[resolve_postimg_direct_url] Could not resolve {page_url}: {e}")
    _resolved_image_cache[page_url] = None
    return None


async def send_photo_safe(bot, chat_id, photo_url_or_page: str, caption: str = None,
                           parse_mode=None, reply_markup=None) -> bool:
    """
    Crash-proof photo sender. Fetches the image bytes server-side (bypasses
    sites like postimg.cc that block Telegram's own URL fetcher with a 403)
    and uploads the raw bytes directly. Falls back to plain URL passthrough,
    then to text-only, so the bot NEVER breaks just because an image failed.
    """
    direct_url = photo_url_or_page
    if "postimg.cc/" in photo_url_or_page and "/i.postimg.cc/" not in photo_url_or_page:
        resolved = resolve_postimg_direct_url(photo_url_or_page)
        direct_url = resolved if resolved else photo_url_or_page

    if direct_url:
        try:
            r = requests.get(direct_url, headers=G_HDR, timeout=10)
            if r.status_code == 200 and r.content:
                photo_bytes = io.BytesIO(r.content)
                photo_bytes.name = "image.jpg"
                await bot.send_photo(chat_id=chat_id, photo=photo_bytes, caption=caption,
                                      parse_mode=parse_mode, reply_markup=reply_markup)
                return True
        except Exception as e:
            logger.warning(f"[send_photo_safe] Byte fetch/upload failed ({e}), trying URL passthrough.")
        try:
            await bot.send_photo(chat_id=chat_id, photo=direct_url, caption=caption,
                                  parse_mode=parse_mode, reply_markup=reply_markup)
            return True
        except Exception as e:
            logger.warning(f"[send_photo_safe] URL passthrough also failed ({e}), falling back to text.")

    if caption:
        try:
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"[send_photo_safe] Text fallback also failed: {e}")
    return False


def q_hash(q: str) -> str:
    return hashlib.md5(q.lower().strip().encode()).hexdigest()[:12]


def game_key(msg_id: int, cid: int) -> str:
    return f"{cid}:{msg_id}"


def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and uid == OWNER_ID


async def is_group_admin(bot, chat_id, user_id) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


def get_user_name(user) -> str:
    if user and user.first_name:
        return user.first_name
    if user and user.username:
        return user.username
    return "buddy"


def analyze_sentiment(text: str) -> tuple:
    try:
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity
        if polarity > 0.3:
            return polarity, random.choice(SENTIMENT_POSITIVE)
        elif polarity < -0.3:
            return polarity, random.choice(SENTIMENT_NEGATIVE)
        else:
            return polarity, random.choice(SENTIMENT_NEUTRAL)
    except Exception:
        return 0.0, "🐾"


def bump_score(cid: str, uid: str, name: str, delta: int) -> int:
    """Synchronous in-memory score update. Caller is responsible for calling
    sync_leaderboard_chat_to_mongo(cid) afterward to persist to MongoDB."""
    db.setdefault("scores", {}).setdefault(cid, {})
    e = db["scores"][cid].get(uid, {"name": name, "user_id": int(uid) if uid.lstrip("-").isdigit() else 0, "score": 0})
    e["name"] = name
    e["score"] = max(0, e["score"] + delta)
    db["scores"][cid][uid] = e
    return e["score"]
