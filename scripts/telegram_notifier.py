"""Fire-and-forget Telegram notifications for the claim/mint pipeline.

Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` from the environment.
Sending happens on a daemon thread so a slow/broken Telegram API never
blocks or fails the calling worker. All errors are swallowed and logged
at WARNING level.

Disable at runtime by setting ``TELEGRAM_NOTIFICATIONS_ENABLED=0``.
"""

from __future__ import annotations

import html
import json
import logging
import os
import threading
from typing import Any, Optional

import requests

try:
    from dotenv import load_dotenv as _load_dotenv  # type: ignore
except Exception:  # pragma: no cover - dotenv is a hard dep, but stay defensive
    _load_dotenv = None  # type: ignore

logger = logging.getLogger(__name__)

_TELEGRAM_SEND_MESSAGE = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_SEND_PHOTO = "https://api.telegram.org/bot{token}/sendPhoto"
_HTTP_TIMEOUT_SECONDS = 10


def _enabled() -> bool:
    flag = os.environ.get("TELEGRAM_NOTIFICATIONS_ENABLED", "1").strip().lower()
    return flag not in {"0", "false", "no", ""}


def _credentials() -> Optional[tuple[str, str]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def _post(url: str, payload: dict) -> None:
    try:
        resp = requests.post(url, json=payload, timeout=_HTTP_TIMEOUT_SECONDS)
        if resp.status_code >= 300:
            logger.warning(
                "Telegram %s failed: %s %s",
                url.rsplit("/", 1)[-1],
                resp.status_code,
                resp.text[:200],
            )
    except Exception as exc:
        logger.warning("Telegram send error: %s", exc)


def _build_view_card_keyboard(link: str) -> Optional[dict]:
    """Inline keyboard with a single ``View on POLYSTARS`` button.

    Returns None when ``link`` is empty so the caller can omit the button.
    Telegram requires public ``http(s)://`` URLs here — the production
    ``CARD_BASE_URL`` (``https://polystars.app``) is fine; private hosts
    like ``localhost`` are rejected by Telegram with HTTP 400.
    """
    if not link:
        return None
    return {
        "inline_keyboard": [[{"text": "View on POLYSTARS", "url": link}]],
    }


def _send_photo(
    token: str,
    chat_id: str,
    photo_url: str,
    caption: str,
    reply_markup: Optional[dict] = None,
) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        # Telegram Bot API expects ``reply_markup`` as a JSON-serialized
        # string regardless of the request Content-Type. Passing it as a
        # nested object works in some clients but is rejected on others
        # with "Bad Request: can't parse reply markup JSON object".
        payload["reply_markup"] = json.dumps(reply_markup)
    _post(_TELEGRAM_SEND_PHOTO.format(token=token), payload)


def _send_message(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: Optional[dict] = None,
) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    _post(_TELEGRAM_SEND_MESSAGE.format(token=token), payload)


def _stringify(value: Any) -> str:
    if value is None:
        return "?"
    text = str(value).strip()
    return text or "?"


# Archetype rarity gradient. Ordered weakest → strongest, matching the
# 1..13 priority ranking in admin_backend/claims_mint.py:_ARCHETYPE_PRIORITY_CASE_SQL.
# A miss returns "" so the "New claim!" line just omits the emoji tail.
_ARCHETYPE_EMOJI: dict[str, str] = {
    "SUBSTRATE":   "💫" * 1,
    "OPERATOR":    "💫" * 2,
    "PASSENGER":   "💫" * 3,
    "BOT":         "💫" * 4,
    "BURNER":      "💫" * 5,
    "EQUILIBRIUM": "💫" * 6,
    "GRAVITON":    "💫" * 7,
    "VECTOR":      "💫" * 8,
    "SIGNAL":      "💫" * 9,
    "EXTRACTOR":   "💫" * 10,
    "ICARUS":      "💫" * 11,
    "ANOMALY":     "💫" * 12,
    "INSIDER":     "💫" * 13,
}


def _archetype_emoji(archetype: Any) -> str:
    key = str(archetype or "").strip().upper()
    return _ARCHETYPE_EMOJI.get(key, "")


def _rewrite_card_url_for_telegram(card_url: str) -> str:
    """Optionally swap the host of the card URL for Telegram-only delivery.

    The on-card QR uses ``CARD_BASE_URL`` (baked into the rendered PNG and
    thus immutable post-mint). For Telegram announcements we sometimes want
    a different base — e.g. pointing at a public dev tunnel
    (cloudflared / ngrok) while testing — without polluting the on-chain
    artifact. Setting ``TELEGRAM_CARD_BASE_URL`` overrides only the
    Telegram link; if unset, the original ``card_url`` is returned
    unchanged.

    Re-reads ``.env`` on each call (``override=False``) so that adding the
    variable mid-session — without restarting uvicorn — still takes effect
    on the next mint notification.

    NOTE: Telegram refuses non-public URLs in inline-keyboard buttons
    (HTTP 400). Make sure the override points at a public ``https://``
    host (a tunnel URL is fine; raw ``http://localhost`` is not).
    """
    if _load_dotenv is not None:
        try:
            _load_dotenv(override=False)
        except Exception:
            pass
    base = os.environ.get("TELEGRAM_CARD_BASE_URL", "").strip().rstrip("/")
    if not base or not card_url:
        return card_url
    marker = "/cards/"
    idx = card_url.find(marker)
    if idx < 0:
        return card_url
    return base + card_url[idx:]


def notify_claim_minted(
    *,
    front_image_url: Optional[str],
    season_type: Any,
    collection_mint_number: Any,
    season_capacity: Any,
    card_url: Optional[str],
    archetype: Any = None,
) -> None:
    """Send a Telegram message announcing a freshly minted claim.

    Posts the rendered front image as a photo with a 3-line caption and
    a "View on POLYSTARS" inline-keyboard button below:
        🚨 NEW CLAIM!  <archetype emoji gradient>
        🎴 Season type: <TYPE>
        💎 Season mint: #<N>/<capacity>
        [ View on POLYSTARS ]   (inline keyboard button → card_url)

    ``card_url`` is taken as-is — it is the production
    ``polystars_card["qr_payload"]`` (``https://polystars.app/cards/<slug>``).

    If the front image URL is missing or Telegram rejects the photo, the
    function falls back to a plain text message so the announcement is
    not lost.
    """
    if not _enabled():
        return
    creds = _credentials()
    if creds is None:
        return
    token, chat_id = creds

    rarity_emoji = _archetype_emoji(archetype)
    headline = "🚨 <b>NEW CLAIM!</b>" + (f" {rarity_emoji}" if rarity_emoji else "")
    season_type_display = html.escape(_stringify(season_type).upper())
    mint_number_display = html.escape(_stringify(collection_mint_number))
    capacity_display = html.escape(_stringify(season_capacity))
    caption = "\n".join([
        headline,
        f"🎴 <b>Season type:</b> {season_type_display}",
        f"💎 <b>Season mint:</b> #{mint_number_display}/{capacity_display}",
    ])

    link = _rewrite_card_url_for_telegram((card_url or "").strip())
    reply_markup = _build_view_card_keyboard(link)
    photo_url = (front_image_url or "").strip()

    def _run() -> None:
        if photo_url:
            _send_photo(token, chat_id, photo_url, caption, reply_markup=reply_markup)
        else:
            _send_message(token, chat_id, caption, reply_markup=reply_markup)

    threading.Thread(target=_run, daemon=True).start()
