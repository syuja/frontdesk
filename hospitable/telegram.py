"""
Telegram delivery for the Hospitable audit digest.

Mirrors the delivery pattern from futbol-report: token + chat_id from env,
sendMessage endpoint, UTF-16-aware truncation at finding boundaries.

The bot token is NEVER logged, printed, or embedded in exception messages.
"""
from __future__ import annotations

import logging
import os

import requests

from hospitable.formatters import TELEGRAM_MAX_UTF16, _utf16_len, truncate_digest

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"

# Default chat ID for the auditor channel — override via TELEGRAM_CHAT_ID env var.
_DEFAULT_CHAT_ID = "8795167083"


def send_digest(digest: str) -> None:
    """Send the audit digest to the configured Telegram channel.

    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
    Truncates at a finding boundary if the digest exceeds the UTF-16 limit.

    Raises RuntimeError if the token is unset.
    Raises requests.HTTPError on HTTP failure.
    The token never appears in logs or exception messages.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", _DEFAULT_CHAT_ID)

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set — cannot deliver digest")

    text = truncate_digest(digest, max_utf16=TELEGRAM_MAX_UTF16)
    units = _utf16_len(text)

    resp = requests.post(
        f"{_TELEGRAM_API}/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    resp.raise_for_status()
    log.info("Digest sent to Telegram (%d UTF-16 units)", units)
