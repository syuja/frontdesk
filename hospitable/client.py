"""
Read-only REST client for the Hospitable v2 API.

All config lives here: base URL, env var name, timeouts, retry policy,
and the messages-endpoint throttle (2 req/min per reservation, confirmed live).

Only GET is exposed. No post/put/patch/delete methods exist, even though
the PAT has write scope.
"""

import logging
import os
import time
from typing import Any

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not needed on Lambda — env vars are set directly

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://public.api.hospitable.com/v2"
PAT_ENV_VAR = "HOSPITABLE_PAT"
REQUEST_TIMEOUT = 20          # seconds per request
MAX_RETRIES = 3               # applies to 429 and 5xx
# Messages endpoint: hard-limited to 2 req/min per reservation (confirmed live
# via X-RateLimit-Limit/Remaining headers in session 0). 31s gap gives a 1s buffer.
_MSG_MIN_INTERVAL = 31.0

log = logging.getLogger(__name__)


# ── Client ────────────────────────────────────────────────────────────────────

class HospitableClient:
    """
    Thin, read-only HTTP client for Hospitable v2.

    Provides:
    - get()        — single GET with 429 + 5xx retry/backoff
    - get_pages()  — auto-paginate via meta.last_page
    - get_messages() — rate-throttled message fetch (2 req/min per reservation)
    """

    def __init__(self) -> None:
        pat = os.environ.get(PAT_ENV_VAR)
        if not pat:
            raise RuntimeError(
                f"{PAT_ENV_VAR} is not set. "
                "Load your .env before creating HospitableClient."
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {pat}",
            "Accept": "application/json",
        })
        # Per-reservation timestamp for the message-endpoint throttle
        self._msg_timestamps: dict[str, float] = {}
        log.debug("HospitableClient initialized")

    # ── Core GET ──────────────────────────────────────────────────────────────

    def get(self, path: str, params: Any = None) -> dict:
        """
        GET BASE_URL+path. Retries on 429 (honors Retry-After) and 5xx
        (exponential backoff). Raises on 4xx (except 429) and on exhausted retries.
        """
        url = BASE_URL + path
        last_resp: requests.Response | None = None

        for attempt in range(MAX_RETRIES):
            resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            last_resp = resp

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                log.warning(
                    "429 rate-limited on %s — waiting %ds (attempt %d/%d)",
                    path, wait, attempt + 1, MAX_RETRIES,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    wait = 2 ** attempt  # 1s, 2s
                    log.warning(
                        "%d on %s — retrying in %ds (attempt %d/%d)",
                        resp.status_code, path, wait, attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue

            resp.raise_for_status()
            return resp.json()

        # All retries exhausted — raise so callers never receive a failed response silently
        last_resp.raise_for_status()  # type: ignore[union-attr]
        return {}  # unreachable; satisfies type checker

    # ── Pagination ────────────────────────────────────────────────────────────

    def get_pages(self, path: str, params: Any = None) -> list[dict]:
        """
        Fetch all pages for a paginated endpoint, following meta.last_page.

        params may be a dict or a list of (key, value) tuples — the latter
        is needed for repeated params like properties[].
        """
        page = 1
        all_items: list[dict] = []

        while True:
            paged_params = _with_page(params, page)
            body = self.get(path, params=paged_params)

            data = body.get("data", [])
            all_items.extend(data if isinstance(data, list) else [data])

            meta = body.get("meta") or {}
            if page >= meta.get("last_page", 1):
                break
            page += 1

        return all_items

    # ── Messages (throttled) ──────────────────────────────────────────────────

    def get_messages(self, reservation_uuid: str) -> dict:
        """
        Fetch messages for a reservation, enforcing 2 req/min per UUID.

        The throttle is per-UUID and per-client-instance. For Lambda (one
        instance, sequential checks), this is sufficient.
        """
        now = time.monotonic()
        last = self._msg_timestamps.get(reservation_uuid, 0.0)
        gap = now - last
        if gap < _MSG_MIN_INTERVAL:
            wait = _MSG_MIN_INTERVAL - gap
            log.debug(
                "Message throttle: sleeping %.1fs for reservation %s…",
                wait, reservation_uuid[:8],
            )
            time.sleep(wait)
        result = self.get(f"/reservations/{reservation_uuid}/messages")
        self._msg_timestamps[reservation_uuid] = time.monotonic()
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _with_page(params: Any, page: int) -> Any:
    """Return params with page set, preserving the list-of-tuples format if used."""
    if isinstance(params, dict):
        return {**params, "page": page}
    # list of (key, value) tuples — filter any existing page param then append
    base = [(k, v) for k, v in (params or []) if k != "page"]
    base.append(("page", str(page)))
    return base
