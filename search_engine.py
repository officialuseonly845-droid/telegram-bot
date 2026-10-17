"""Web + image retrieval for /search, /ss and /bananalogic.

Why this exists: scraping google.com directly gets CAPTCHA-walled/blocked almost
immediately on cloud hosts, which is why /search kept falling back. This module
uses providers in order of reliability and returns as soon as one works:

  TEXT   Google Programmable Search API (if keys set) -> Brave API (if key set)
         -> DuckDuckGo HTML -> DuckDuckGo Lite -> Bing
  IMAGES Google Programmable Search images (if keys set) -> DuckDuckGo images
         -> Bing images -> Openverse -> Wikimedia Commons

Everything here is blocking (requests), so call it through run_in_executor.
"""
import base64
import io
import json
import re
import time
import urllib.parse
from typing import Optional
import requests
from bs4 import BeautifulSoup
from config import BRAVE_API_KEY, GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_CX, logger

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 10

_cache: dict = {}          # (kind, query) -> (timestamp, value)
_CACHE_TTL = 600
_CACHE_MAX = 150


def _cache_get(kind: str, query: str):
    hit = _cache.get((kind, query.lower()))
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def _cache_put(kind: str, query: str, value):
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[(kind, query.lower())] = (time.time(), value)


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()


def _result(title, url, snippet) -> dict:
    return {"title": _clean(title)[:160], "url": url, "snippet": _clean(snippet)[:400]}


# ───────────────────────────── text providers ─────────────────────────────

def _google_cse(query: str, n: int) -> list:
    if not (GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX):
        return []
    r = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": GOOGLE_SEARCH_API_KEY, "cx": GOOGLE_SEARCH_CX, "q": query, "num": min(n, 10)},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return [_result(i.get("title"), i.get("link"), i.get("snippet"))
            for i in r.json().get("items", []) if i.get("link")]


def _brave(query: str, n: int) -> list:
    if not BRAVE_API_KEY:
        return []
    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(n, 20)},
        headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return [_result(i.get("title"), i.get("url"), i.get("description"))
            for i in r.json().get("web", {}).get("results", []) if i.get("url")]


def _ddg_real_url(href: str) -> str:
    """DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<real url>. Unwrap it."""
    if href.startswith("//"):
        href = "https:" + href
    p = urllib.parse.urlparse(href)
    if "duckduckgo.com" in p.netloc and p.path.startswith("/l/"):
        v = urllib.parse.parse_qs(p.query).get("uddg")
        if v:
            return v[0]
    return href


def parse_ddg_html(html: str, n: int) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for div in soup.select("div.result"):
        if "result--ad" in (div.get("class") or []):
            continue
        a = div.select_one("a.result__a")
        if not a or not a.get("href"):
            continue
        sn = div.select_one(".result__snippet")
        out.append(_result(a.get_text(" "), _ddg_real_url(a["href"]), sn.get_text(" ") if sn else ""))
        if len(out) >= n:
            break
    return out


def _ddg_html(query: str, n: int) -> list:
    r = requests.post(
        "https://html.duckduckgo.com/html/", data={"q": query, "kl": "wt-wt"},
        headers={**_HEADERS, "Referer": "https://html.duckduckgo.com/", "Origin": "https://html.duckduckgo.com"},
        timeout=_TIMEOUT,
    )
    if r.status_code != 200 or "anomaly" in r.text[:6000]:
        raise RuntimeError(f"ddg html blocked (HTTP {r.status_code})")
    return parse_ddg_html(r.text, n)


def parse_ddg_lite(html: str, n: int) -> list:
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("a.result-link")
    snips = soup.select("td.result-snippet")
    out = []
    for i, a in enumerate(links):
        if not a.get("href"):
            continue
        sn = snips[i].get_text(" ") if i < len(snips) else ""
        out.append(_result(a.get_text(" "), _ddg_real_url(a["href"]), sn))
        if len(out) >= n:
            break
    return out


def _ddg_lite(query: str, n: int) -> list:
    r = requests.post(
        "https://lite.duckduckgo.com/lite/", data={"q": query, "kl": "wt-wt"},
        headers={**_HEADERS, "Referer": "https://lite.duckduckgo.com/"}, timeout=_TIMEOUT,
    )
    if r.status_code != 200 or "anomaly" in r.text[:6000]:
        raise RuntimeError(f"ddg lite blocked (HTTP {r.status_code})")
    return parse_ddg_lite(r.text, n)


def _bing_real_url(href: str) -> str:
    """Bing wraps results as bing.com/ck/a?...&u=a1<base64 url>. Unwrap it."""
    try:
        p = urllib.parse.urlparse(href)
        if "bing.com" in p.netloc and p.path.startswith("/ck/"):
            u = urllib.parse.parse_qs(p.query).get("u", [""])[0]
            if u.startswith("a1"):
                b = u[2:]
                return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf8")
    except Exception:
        pass
    return href


def parse_bing_html(html: str, n: int) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        p = li.select_one(".b_caption p") or li.select_one("p")
        out.append(_result(a.get_text(" "), _bing_real_url(a["href"]), p.get_text(" ") if p else ""))
        if len(out) >= n:
            break
    return out


def _bing(query: str, n: int) -> list:
    r = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "setlang": "en-US", "cc": "US", "count": min(n + 4, 15)},
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"bing HTTP {r.status_code}")
    return parse_bing_html(r.text, n)


_TEXT_PROVIDERS = [("google-api", _google_cse), ("brave", _brave), ("duckduckgo", _ddg_html),
                   ("duckduckgo-lite", _ddg_lite), ("bing", _bing)]


def text_search(query: str, n: int = 6) -> dict:
    """Returns {"found", "ai_answer", "snippets", "results", "source"}.
    `snippets` keeps the old shape (list of short strings) for the summariser."""
    cached = _cache_get("text", query)
    if cached is not None:
        return cached
    out = {"found": False, "ai_answer": "", "snippets": [], "results": [], "source": ""}
    errors = []
    for name, fn in _TEXT_PROVIDERS:
        try:
            res = fn(query, n)
        except Exception as e:
            errors.append(f"{name}: {str(e)[:70]}")
            continue
        res = [r for r in res if r.get("url") and (r.get("snippet") or r.get("title"))]
        if res:
            out.update({
                "found": True, "results": res, "source": name,
                "snippets": [(f"{r['title']}: {r['snippet']}" if r["snippet"] else r["title"])[:320] for r in res[:6]],
            })
            _cache_put("text", query, out)
            return out
    if errors:
        logger.warning(f"[search] all text providers failed for {query!r}: {' | '.join(errors)}")
    return out


# ───────────────────────────── image providers ─────────────────────────────

def _google_cse_images(query: str, n: int) -> list:
    if not (GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX):
        return []
    r = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": GOOGLE_SEARCH_API_KEY, "cx": GOOGLE_SEARCH_CX, "q": query,
                "searchType": "image", "num": min(n, 10), "safe": "active"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return [i["link"] for i in r.json().get("items", []) if i.get("link")]


def _ddg_images(query: str, n: int) -> list:
    s = requests.Session()
    s.headers.update(_HEADERS)
    r = s.get("https://duckduckgo.com/", params={"q": query, "iax": "images", "ia": "images"}, timeout=_TIMEOUT)
    m = re.search(r"vqd=[\"']?([\d-]+)", r.text)
    if not m:
        raise RuntimeError("ddg images: no vqd token")
    r2 = s.get(
        "https://duckduckgo.com/i.js",
        params={"l": "us-en", "o": "json", "q": query, "vqd": m.group(1), "f": ",,,,,", "p": "1"},
        headers={"Referer": "https://duckduckgo.com/", "Accept": "application/json"}, timeout=_TIMEOUT,
    )
    if r2.status_code != 200:
        raise RuntimeError(f"ddg images HTTP {r2.status_code}")
    return [x["image"] for x in r2.json().get("results", []) if x.get("image")][:n]


def parse_bing_images(html: str, n: int) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a.iusc"):
        try:
            m = json.loads(a.get("m", "{}"))
        except Exception:
            continue
        if m.get("murl"):
            out.append(m["murl"])
        if len(out) >= n:
            break
    return out


def _bing_images(query: str, n: int) -> list:
    r = requests.get(
        "https://www.bing.com/images/search",
        params={"q": query, "form": "HDRSC2", "first": 1, "adlt": "strict"},
        headers=_HEADERS, timeout=_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"bing images HTTP {r.status_code}")
    return parse_bing_images(r.text, n)


def _openverse(query: str, n: int) -> list:
    r = requests.get("https://api.openverse.org/v1/images/",
                     params={"q": query, "page_size": min(n, 20), "mature": "false"},
                     headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("results", []) if x.get("url")]


def _commons(query: str, n: int) -> list:
    r = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params={"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query,
                "gsrlimit": min(n, 10), "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 1280, "format": "json"},
        headers={"User-Agent": "BelugaBot/12 (Telegram bot)"}, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    out = []
    pages = sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") in ("image/jpeg", "image/png", "image/webp"):
            out.append(info.get("thumburl") or info.get("url"))
    return [u for u in out if u]


_IMAGE_PROVIDERS = [("google-api", _google_cse_images), ("duckduckgo", _ddg_images), ("bing", _bing_images),
                    ("openverse", _openverse), ("commons", _commons)]


def image_search(query: str, n: int = 8) -> list:
    """Candidate image URLs, best first. Several providers are merged until there are
    enough candidates, because any single hot-linked URL can fail to download."""
    cached = _cache_get("img", query)
    if cached is not None:
        return list(cached)
    urls, errors = [], []
    for name, fn in _IMAGE_PROVIDERS:
        try:
            for u in fn(query, n):
                if u and u not in urls:
                    urls.append(u)
        except Exception as e:
            errors.append(f"{name}: {str(e)[:70]}")
        if len(urls) >= n:
            break
    if not urls and errors:
        logger.warning(f"[search] all image providers failed for {query!r}: {' | '.join(errors)}")
    if urls:
        _cache_put("img", query, urls)
    return urls[:n]


def fetch_image_bytes(url: str, max_bytes: int = 8_000_000) -> Optional[bytes]:
    """Downloads a candidate and returns bytes Telegram will accept (JPEG/PNG), or None.
    Downloading server-side beats handing Telegram the URL: many hosts block Telegram's
    fetcher or hot-linking. Rejects tiny icons, extreme banners, HTML pages, SVGs."""
    try:
        p = urllib.parse.urlparse(url)
        r = requests.get(
            url, stream=True, timeout=_TIMEOUT,
            headers={"User-Agent": _UA, "Accept": "image/*,*/*;q=0.5", "Referer": f"{p.scheme}://{p.netloc}/"},
        )
        if r.status_code != 200:
            return None
        ct = r.headers.get("Content-Type", "").lower()
        if ct and not (ct.startswith("image/") or "octet-stream" in ct):
            return None
        buf, total = [], 0
        for chunk in r.iter_content(65536):
            total += len(chunk)
            if total > max_bytes:
                return None
            buf.append(chunk)
        data = b"".join(buf)
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        w, h = im.size
        if w < 200 or h < 200 or max(w, h) / max(1, min(w, h)) > 15:
            return None
        if im.format in ("JPEG", "PNG") and w + h <= 9500 and len(data) <= 9_000_000:
            return data
        if w + h > 9500:
            scale = 9000 / (w + h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue()
    except Exception:
        return None
