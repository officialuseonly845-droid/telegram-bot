"""AI providers (Cohere, Cloudflare, Groq, OpenRouter, NVIDIA), rate-limit/fallback logic, and the ai() entry point."""
import asyncio
import os
import re
import time
import aiohttp
from typing import Optional
from config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_API_TOKEN,
    GROQ_KEY,
    NVIDIA_KEY,
    OPENROUTER_KEY,
    logger,
)
from state import bot_status
from texts import CHAT_PROMPT, CHAT_PROMPT_OR


GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
OR_MODEL = "google/gemma-4-26b-a4b-it:free"
OR_BASE = "https://openrouter.ai/api/v1"
NVIDIA_MODEL = "meta/muse-glimmer-30b"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"


def _env(*names: str) -> str:
    """First non-empty environment variable among `names` (whitespace / stray quotes stripped)."""
    for n in names:
        v = os.environ.get(n, "").strip().strip("\"'").strip()
        if v:
            return v
    return ""


# ── Cloudflare Workers AI ────────────────────────────────────────────────────
# Needs an Account ID + an API token. Any of these env names work:
#   token:      CLOUDFLARE_API_TOKEN | CLOUDFLARE_AUTH_TOKEN | CLOUDFLARE_API_KEY | CF_API_TOKEN
#   account id: CLOUDFLARE_ACCOUNT_ID | CF_ACCOUNT_ID | CLOUDFLARE_ACCOUNT
_CF_TOKEN = CLOUDFLARE_API_TOKEN or _env("CLOUDFLARE_AUTH_TOKEN", "CLOUDFLARE_API_KEY", "CF_API_TOKEN")
_CF_ACCOUNT = CLOUDFLARE_ACCOUNT_ID or _env("CF_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT")
CF_MODEL = _env("CLOUDFLARE_MODEL") or "@cf/meta/llama-3.2-3b-instruct"

# ── Cohere ───────────────────────────────────────────────────────────────────
# Env: COHERE_API_KEY (or CO_API_KEY). Model can be changed with COHERE_MODEL.
COHERE_KEY = _env("COHERE_API_KEY", "CO_API_KEY", "COHERE_KEY")
COHERE_MODEL = _env("COHERE_MODEL") or "command-a-plus-05-2026"
COHERE_URL = "https://api.cohere.com/v2/chat"

# Order "auto" mode tries providers in. Providers whose key isn't set are skipped
# automatically, so you can just set the keys you have. Reorder freely:
#   "co" = Cohere, "cf" = Cloudflare, "groq", "or" = OpenRouter, "nvi" = NVIDIA
AUTO_ORDER = ["co", "cf", "groq", "or", "nvi"]
# The tiny emoji-reaction pick runs on almost every message, so it prefers the fast/cheap providers.
EMOJI_ORDER = ["groq", "cf", "co"]


# Default mode is "auto": tries Cohere -> Cloudflare -> Groq -> OpenRouter -> NVIDIA (see AUTO_ORDER),
# skipping any provider whose API key isn't set, and does a second full pass before giving up, so one
# provider having a rough moment doesn't reach the user as the fallback "Meow!" reply.
# Switch to a single provider anytime with /model (owner-only).
ai_model_state = {
    "mode": "auto",
    "groq_rl_until": 0.0, "or_rl_until": 0.0, "nvi_rl_until": 0.0, "cf_rl_until": 0.0, "co_rl_until": 0.0,
}


def _groq_rate_limited() -> bool:
    return time.time() < ai_model_state["groq_rl_until"]


def _or_rate_limited() -> bool:
    return time.time() < ai_model_state["or_rl_until"]


def _nvi_rate_limited() -> bool:
    return time.time() < ai_model_state["nvi_rl_until"]


def _cf_rate_limited() -> bool:
    return time.time() < ai_model_state["cf_rl_until"]


def _co_rate_limited() -> bool:
    return time.time() < ai_model_state["co_rl_until"]


def _set_groq_rl():
    ai_model_state["groq_rl_until"] = time.time() + 20
    logger.warning("[AI] Groq rate-limited — backing off 20s")


def _set_or_rl():
    ai_model_state["or_rl_until"] = time.time() + 20
    logger.warning("[AI] OpenRouter rate-limited — backing off 20s")


def _set_nvi_rl():
    ai_model_state["nvi_rl_until"] = time.time() + 20
    logger.warning("[AI] NVIDIA rate-limited — backing off 20s")


def _set_cf_rl():
    ai_model_state["cf_rl_until"] = time.time() + 20
    logger.warning("[AI] Cloudflare rate-limited — backing off 20s")


def _set_co_rl():
    ai_model_state["co_rl_until"] = time.time() + 20
    logger.warning("[AI] Cohere rate-limited — backing off 20s")


async def _call_groq(system: str, user: str, max_tok: int) -> Optional[str]:
    if not GROQ_KEY:
        logger.warning("[AI] GROQ_API_KEY not set")
        return None
    bot_status["api_calls"] += 1
    # gpt-oss models spend part of max_tokens on internal reasoning before
    # producing the visible answer. If max_tokens is too small, the whole
    # budget gets eaten by reasoning and content comes back empty/truncated.
    # reasoning_effort="low" keeps more of the budget for the actual reply.
    safe_max_tok = max(max_tok, 60)
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": GROQ_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": safe_max_tok,
                "reasoning_effort": "low",
            }
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    choice = data["choices"][0]
                    content = choice["message"]["content"].strip()
                    if not content and choice.get("finish_reason") == "length":
                        logger.warning("[AI] Groq: response truncated by max_tokens (reasoning ate the budget)")
                    return content or None
                elif r.status == 429:
                    _set_groq_rl()
                    return None
                else:
                    body = await r.text()
                    logger.error(f"[AI] Groq error {r.status}: {body[:300]}")
                    bot_status["failed_apis"] += 1
    except Exception as e:
        logger.error(f"[AI] Groq exception: {e}")
        bot_status["failed_apis"] += 1
    return None


async def _call_openrouter(system: str, user: str, max_tok: int) -> Optional[str]:
    if not OPENROUTER_KEY:
        logger.warning("[AI] OPENROUTER_API_KEY not set")
        return None
    bot_status["api_calls"] += 1
    safe_max_tok = max(max_tok, 60)
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": OR_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": safe_max_tok
            }
            async with session.post(
                f"{OR_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://t.me/BELUGAPY",
                    "X-Title": "BelugaBot"
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    choice = data["choices"][0]
                    content = choice["message"]["content"].strip()
                    if not content and choice.get("finish_reason") == "length":
                        logger.warning("[AI] OpenRouter: response truncated by max_tokens")
                    return content or None
                elif r.status == 429:
                    _set_or_rl()
                    return None
                else:
                    body = await r.text()
                    logger.error(f"[AI] OpenRouter error {r.status}: {body[:300]}")
                    bot_status["failed_apis"] += 1
    except Exception as e:
        logger.error(f"[AI] OpenRouter exception: {e}")
        bot_status["failed_apis"] += 1
    return None


async def _call_nvidia(system: str, user: str, max_tok: int) -> Optional[str]:
    """
    Calls NVIDIA's OpenAI-compatible endpoint (integrate.api.nvidia.com) with
    the meta/muse-glimmer-30b model. Implemented as a plain aiohttp POST
    (same pattern as _call_groq / _call_openrouter above) instead of the
    `openai` Python SDK — it's the same REST call the SDK makes under the
    hood, and this way no new pip dependency is needed. If you'd rather use
    the official `openai` client, add `openai` to requirements.txt and swap
    the aiohttp block below for the OpenAI() client call.

    muse-glimmer-30b is a REASONING model on NVIDIA's NIM stack — by default
    it "thinks" first via a separate reasoning_content field, and if it
    doesn't finish thinking before hitting max_tokens, message.content comes
    back as null (not ""), which used to crash .strip(). chat_template_kwargs
    below tells the template to skip the thinking step so content is filled
    directly; we still handle a null/empty content defensively in case a
    future model swap re-introduces reasoning-only replies.

    It's also a free NIM endpoint, which blips with timeouts / 5xx / empty
    choices more than a paid Groq-style endpoint does — that's the
    "sometimes fails" symptom. So this does ONE quick internal retry on a
    transient failure before giving up, separate from ai()'s own 3x retry
    loop in locked mode (that loop calls this whole function again from
    scratch, which is slower; this inner retry catches the common blip fast).
    """
    if not NVIDIA_KEY:
        logger.warning("[AI] NVIDIA_API_KEY not set")
        return None
    bot_status["api_calls"] += 1
    # Reasoning models can eat a small budget entirely on hidden thinking
    # tokens before ever reaching content, even with thinking nominally off —
    # give it more headroom than Groq/OR get.
    safe_max_tok = max(max_tok, 512)
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": safe_max_tok,
        "stream": False,
        # Different NIM model families read different keys here ("thinking"
        # for Nemotron/Granite-style, "enable_thinking" for DeepSeek/GLM-
        # style); unrecognized keys are ignored, so sending both is safe and
        # covers muse-glimmer either way.
        "chat_template_kwargs": {"thinking": False, "enable_thinking": False},
    }
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{NVIDIA_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {NVIDIA_KEY}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=16)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        choices = data.get("choices") or []
                        if not choices:
                            logger.warning(f"[AI] NVIDIA: empty choices list (attempt {attempt+1}), raw={str(data)[:200]}")
                        else:
                            msg = choices[0].get("message", {}) or {}
                            content = (msg.get("content") or "").strip()
                            if not content:
                                # Fell back to reasoning-only output — use it
                                # rather than returning nothing.
                                reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
                                if reasoning:
                                    logger.warning("[AI] NVIDIA: content empty, using reasoning_content as fallback")
                                    content = reasoning
                                else:
                                    logger.warning(f"[AI] NVIDIA: empty content (attempt {attempt+1}), finish_reason={choices[0].get('finish_reason')}, raw_message={msg}")
                            if content:
                                return content
                            # else: fall through to the inner retry below
                    elif r.status == 429:
                        _set_nvi_rl()
                        return None
                    elif r.status >= 500:
                        body = await r.text()
                        logger.warning(f"[AI] NVIDIA server error {r.status} (attempt {attempt+1}): {body[:200]}")
                    else:
                        body = await r.text()
                        logger.error(f"[AI] NVIDIA error {r.status}: {body[:300]}")
                        bot_status["failed_apis"] += 1
                        return None
        except asyncio.TimeoutError:
            logger.warning(f"[AI] NVIDIA timed out (attempt {attempt+1})")
        except Exception as e:
            logger.warning(f"[AI] NVIDIA exception (attempt {attempt+1}): {e}")
        if attempt == 0:
            await asyncio.sleep(0.6)  # brief backoff before the one inner retry
    bot_status["failed_apis"] += 1
    return None


def _cf_extract(data: dict) -> str:
    """Pulls the reply text out of a Workers AI response. Classic models answer with
    result.response; newer OpenAI-style models answer with result.choices[0].message.content."""
    res = data.get("result") or {}
    txt = res.get("response")
    if isinstance(txt, str) and txt.strip():
        return txt.strip()
    choices = res.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


async def _call_cloudflare(system: str, user: str, max_tok: int) -> Optional[str]:
    """
    Calls Cloudflare Workers AI: POST /accounts/{account_id}/ai/run/{model} with a Bearer token.
    Env: account id (CLOUDFLARE_ACCOUNT_ID) + API token (CLOUDFLARE_API_TOKEN), see the aliases at the top.
    Model: CF_MODEL (default @cf/meta/llama-3.2-3b-instruct, override with CLOUDFLARE_MODEL).
    """
    if not _CF_TOKEN or not _CF_ACCOUNT:
        logger.warning("[AI] Cloudflare token / account id not set (CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID)")
        return None
    bot_status["api_calls"] += 1
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max(max_tok, 60),
            }
            async with session.post(
                f"https://api.cloudflare.com/client/v4/accounts/{_CF_ACCOUNT}/ai/run/{CF_MODEL}",
                headers={"Authorization": f"Bearer {_CF_TOKEN}", "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=16)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if not data.get("success", True):
                        logger.error(f"[AI] Cloudflare error payload: {str(data.get('errors'))[:200]}")
                        bot_status["failed_apis"] += 1
                        return None
                    content = _cf_extract(data)
                    if not content:
                        logger.warning(f"[AI] Cloudflare: empty reply, raw={str(data)[:200]}")
                    return content or None
                elif r.status == 429:
                    _set_cf_rl()
                    return None
                elif r.status == 401:
                    logger.error(
                        "[AI] Cloudflare 401 Authentication error — this is Cloudflare rejecting "
                        "the API token / account id, not a bot bug. Check: (1) it's a scoped API Token "
                        "(My Profile > API Tokens), not the legacy Global API Key; (2) the token has "
                        "Account > Workers AI > Read/Edit permission; (3) the account id matches the "
                        "account the token was created under."
                    )
                    bot_status["failed_apis"] += 1
                    return None
                else:
                    body = await r.text()
                    hint = " (model name not found — check CLOUDFLARE_MODEL)" if ("5007" in body or "No such model" in body) else ""
                    logger.error(f"[AI] Cloudflare error {r.status}{hint}: {body[:300]}")
                    bot_status["failed_apis"] += 1
    except Exception as e:
        logger.error(f"[AI] Cloudflare exception: {e}")
        bot_status["failed_apis"] += 1
    return None


def _cohere_extract(data: dict) -> str:
    """Cohere v2 returns message.content as a list of blocks; keep only the visible text
    blocks (reasoning models may also return 'thinking' blocks, which must not be shown)."""
    content = (data.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    parts = []
    for blk in content or []:
        if isinstance(blk, dict) and blk.get("type", "text") == "text" and isinstance(blk.get("text"), str):
            parts.append(blk["text"])
    return "\n".join(parts).strip()


async def _call_cohere(system: str, user: str, max_tok: int) -> Optional[str]:
    """
    Calls Cohere's v2 chat API (https://api.cohere.com/v2/chat) with COHERE_MODEL
    (default command-a-plus-05-2026). Env: COHERE_API_KEY.
    """
    if not COHERE_KEY:
        logger.warning("[AI] COHERE_API_KEY not set")
        return None
    bot_status["api_calls"] += 1
    # Floor of 256: if the model spends part of its budget on internal reasoning before the visible
    # answer, a tiny max_tokens could otherwise leave the reply empty. Replies stay short because
    # the prompts ask for short replies; the floor only prevents truncation.
    payload = {
        "model": COHERE_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max(max_tok, 256),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                COHERE_URL,
                headers={"Authorization": f"Bearer {COHERE_KEY}", "Content-Type": "application/json",
                         "Accept": "application/json", "X-Client-Name": "beluga-bot"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    content = _cohere_extract(data)
                    if not content and data.get("finish_reason") == "MAX_TOKENS":
                        logger.warning("[AI] Cohere: response truncated by max_tokens")
                    elif not content:
                        logger.warning(f"[AI] Cohere: empty reply, raw={str(data)[:200]}")
                    return content or None
                elif r.status == 429:
                    _set_co_rl()
                    return None
                elif r.status in (401, 403):
                    logger.error("[AI] Cohere auth error — check COHERE_API_KEY (use a Trial/Production key from dashboard.cohere.com).")
                    bot_status["failed_apis"] += 1
                    return None
                else:
                    body = await r.text()
                    hint = f" (model '{COHERE_MODEL}' rejected — check COHERE_MODEL)" if r.status in (400, 404) else ""
                    logger.error(f"[AI] Cohere error {r.status}{hint}: {body[:300]}")
                    bot_status["failed_apis"] += 1
    except Exception as e:
        logger.error(f"[AI] Cohere exception: {e}")
        bot_status["failed_apis"] += 1
    return None


def _ready(provider: str) -> bool:
    """True if the provider has the credentials it needs."""
    return {
        "co": bool(COHERE_KEY),
        "cf": bool(_CF_TOKEN and _CF_ACCOUNT),
        "groq": bool(GROQ_KEY),
        "or": bool(OPENROUTER_KEY),
        "nvi": bool(NVIDIA_KEY),
    }.get(provider, False)


_PROVIDER_NAMES = {"co": "Cohere", "cf": "Cloudflare", "groq": "Groq", "or": "OpenRouter", "nvi": "NVIDIA"}
logger.info("[AI] Providers in auto order: " + (" → ".join(_PROVIDER_NAMES[p] for p in AUTO_ORDER if _ready(p))
                                                  or "NONE configured (set COHERE_API_KEY and/or Cloudflare keys)"))

# /model (owner.py) mode name -> provider. "co" is Cohere.
_LOCKED_MODES = {"gro": "groq", "rou": "or", "nvi": "nvi", "cf": "cf", "co": "co"}


async def _dispatch(provider: str, system: str, user: str, max_tok: int) -> Optional[str]:
    """One attempt on one provider, honouring its rate-limit back-off and timeout."""
    if provider == "groq":
        if _groq_rate_limited():
            return None
        return await asyncio.wait_for(_call_groq(system, user, max_tok), timeout=14)
    if provider == "nvi":
        if _nvi_rate_limited():
            return None
        # 34s, not 18s: _call_nvidia does its own inner retry (up to
        # 2 attempts x 16s + a short backoff) to absorb the free
        # endpoint's occasional blips before giving up. Cutting the
        # outer timeout short would kill that retry mid-flight.
        return await asyncio.wait_for(_call_nvidia(system, user, max_tok), timeout=34)
    if provider == "cf":
        if _cf_rate_limited():
            return None
        return await asyncio.wait_for(_call_cloudflare(system, user, max_tok), timeout=16)
    if provider == "co":
        if _co_rate_limited():
            return None
        return await asyncio.wait_for(_call_cohere(system, user, max_tok), timeout=22)
    # OpenRouter
    if _or_rate_limited():
        return None
    or_system = CHAT_PROMPT_OR if system.startswith(CHAT_PROMPT) else system
    return await asyncio.wait_for(_call_openrouter(or_system, user, max_tok), timeout=16)


async def ai(system: str, user: str, fallback: str = "Meow! 🐾", max_tok: int = 200) -> str:
    """
    Smart multi-provider AI call.
    Locked modes ('gro', 'rou', 'nvi', 'cf', 'co') → that ONE provider only, retried up to 3x, never switches away.
    Mode 'auto' → tries the providers in AUTO_ORDER (Cohere, Cloudflare, Groq, OpenRouter, NVIDIA) that have
                  an API key set, then a second full pass before returning `fallback`.
    """
    mode = ai_model_state["mode"]

    async def _try(provider: str) -> Optional[str]:
        try:
            return await _dispatch(provider, system, user, max_tok)
        except asyncio.TimeoutError:
            logger.warning(f"[AI] {provider} timed out")
        except Exception as e:
            logger.warning(f"[AI] {provider} error: {e}")
        return None

    if mode in _LOCKED_MODES:
        # Locked to one provider — retry only that provider, never cross over.
        provider = _LOCKED_MODES[mode]
        for attempt in range(3):
            res = await _try(provider)
            if res:
                if attempt > 0:
                    logger.info(f"[AI] {provider} succeeded on attempt {attempt+1}")
                return res
        logger.error(f"[AI] {provider} failed 3x in locked mode — returning fallback")
        return fallback

    # auto mode: try every configured provider, then a second full pass across all of them.
    order = [p for p in AUTO_ORDER if _ready(p)] or list(AUTO_ORDER)
    for provider in order:
        res = await _try(provider)
        if res:
            return res

    for provider in order:
        res = await _try(provider)
        if res:
            logger.info(f"[AI] {provider} succeeded on retry pass")
            return res

    logger.error("[AI] All providers failed twice — returning fallback")
    return fallback


async def ai_emoji(text: str) -> str:
    """Quick emoji pick. Uses the first available provider in EMOJI_ORDER (fast/cheap ones first)."""
    system = "Output ONE emoji matching emotion. ONLY the emoji, nothing else."
    user = f"Text: '{text[:60]}'"
    for provider in [p for p in EMOJI_ORDER if _ready(p)]:
        try:
            res = await asyncio.wait_for(_dispatch(provider, system, user, 10), timeout=8)
        except Exception:
            res = None
        if res:
            found = re.findall(r"[^\w\s,.:!?'\"\(\)\-]+", res)
            if found:
                return found[0][0]
    return "😼"


async def _call_groq_vision(image_b64: str, question: str) -> Optional[str]:
    """Sends an image straight to Groq's vision-capable chat model. No local
    ML at all (no torch/YOLO/opencv-DNN) — this is a single lightweight API
    call, which is what makes 'seeing' images possible on Render's free
    tier where heavy ML libraries simply won't fit in RAM."""
    if not GROQ_KEY:
        return None
    payload = {
        "model": GROQ_VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ],
        }],
        "max_tokens": 200,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json=payload, timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data["choices"][0]["message"]["content"].strip()
                logger.error(f"[vision] Groq error {r.status}: {(await r.text())[:200]}")
    except Exception as e:
        logger.error(f"[vision] {e}")
    return None


NVIDIA_VISION_MODEL = "deepseek-ai/deepseek-v4.1-flash"


async def _call_nvidia_vision(image_b64_list: list, question: str, max_tok: int = 2048) -> Optional[str]:
    """
    Sends one or more images (a photo, or a representative frame from a
    video/video-sticker) to NVIDIA's deepseek-ai/deepseek-v4.1-flash.
    Confirmed via its model card on build.nvidia.com: this is a natively
    multimodal MoE model (Data Modality: Image, Text) — not the earlier
    diffusiongemma model, which turned out to be text-only despite its demo
    code including an image_url block. Same aiohttp-POST pattern as the
    other NVIDIA/Groq calls in this file.

    No chat_template_kwargs here — NVIDIA's own sample for this model
    doesn't set one, and "continuously controllable reasoning effort" per
    its model card is opt-in, not on by default the way muse-glimmer's was.
    Still give it a generous token budget as a safety margin.
    """
    if not NVIDIA_KEY or not image_b64_list:
        return None
    bot_status["api_calls"] += 1
    content = [{"type": "text", "text": question}]
    for b64 in image_b64_list:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    payload = {
        "model": NVIDIA_VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": max(max_tok, 512),
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{NVIDIA_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {NVIDIA_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=25)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    choices = data.get("choices") or []
                    if not choices:
                        logger.warning(f"[vision] NVIDIA: empty choices, raw={str(data)[:200]}")
                        return None
                    msg = choices[0].get("message", {}) or {}
                    text = (msg.get("content") or "").strip()
                    if not text:
                        text = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
                    return text or None
                elif r.status == 429:
                    logger.warning("[vision] NVIDIA rate-limited")
                else:
                    body = await r.text()
                    logger.error(f"[vision] NVIDIA error {r.status}: {body[:300]}")
    except Exception as e:
        logger.error(f"[vision] NVIDIA exception: {e}")
    return None


async def _call_vision(image_b64_list: list, question: str) -> Optional[str]:
    """
    Vision dispatcher: NVIDIA's deepseek-v4.1-flash first (that's the model
    this feature is built around), Groq's llama-4-scout as a fallback if
    NVIDIA is unavailable/unconfigured/fails — mirrors the multi-provider
    pattern ai() already uses for text chat, so one provider having a bad
    moment doesn't mean "couldn't analyze that" for the user.
    """
    result = await _call_nvidia_vision(image_b64_list, question)
    if result:
        return result
    # Groq's vision endpoint only accepts a single image per call.
    if image_b64_list:
        result = await _call_groq_vision(image_b64_list[0], question)
    return result
