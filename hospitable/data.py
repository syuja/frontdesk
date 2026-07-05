"""
Data-pull layer for the Hospitable auditor.

These are the functions the check modules call. Each one:
- Handles the known API quirks (properties[] required, deprecated fields, etc.)
- Returns clean Python dicts, not raw response JSON
- Centralizes field-access gotchas so checks don't re-implement them

Field gotchas centralized here:
- Reservation status: reservation_status.current.category — the flat 'status'
  field is deprecated. Access via res_status(r) or the normalized 'status' key.
- Message sender: sender_type is the canonical field; sender/author are fallbacks
  in case the shape differs between reservation and inquiry message endpoints.
- Property UUID: the 'id' field holds the UUID (not 'uuid').
"""

import datetime
import logging
from hospitable.client import HospitableClient

log = logging.getLogger(__name__)

# Default includes for reservation pulls — centralized so checks can declare
# which fields they depend on without re-spelling the include string.
RESERVATION_INCLUDES = "financials,guest,listings,properties,review"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _qs(key: str, values: list[str]) -> list[tuple[str, str]]:
    """Build repeated query-param tuples, e.g. ('properties[]', uuid) × n."""
    return [(key, v) for v in values]


def _uuid(obj: dict) -> str:
    """Hospitable uses 'id' as the UUID field. Fall back to 'uuid' if absent."""
    return str(obj.get("id") or obj.get("uuid") or "")


def res_status(res: dict) -> str | None:
    """
    Extract the canonical reservation status.

    The flat 'status' field is deprecated — the actual value lives at
    reservation_status.current.category. This helper (and the normalized
    'status' key added by get_reservations) is the only place that path
    is spelled out.
    """
    return (
        ((res.get("reservation_status") or {}).get("current") or {}).get("category")
    )


def msg_sender(msg: dict) -> str | None:
    """
    Extract the canonical sender from a message object.

    sender_type is the documented field. The fallbacks cover any shape
    difference between /reservations/{uuid}/messages and /inquiries/{uuid}.
    """
    return msg.get("sender_type") or msg.get("sender") or msg.get("author")


# ── Data pull functions ───────────────────────────────────────────────────────

def get_properties(client: HospitableClient) -> list[dict]:
    """
    All properties in the account.

    Each record includes:
    - uuid         (str)  — normalized from 'id'
    - parent_child (str|None) — null / "parent" / "child"
    - listings     (list) — child listing objects for parent properties

    properties[] is not required on /properties — it returns all.
    """
    items = client.get_pages(
        "/properties",
        params=[("include", "listings"), ("per_page", "100")],
    )
    return [
        {
            **p,
            "uuid": _uuid(p),
        }
        for p in items
    ]


def get_reservations(
    client: HospitableClient,
    property_uuids: list[str],
    start: datetime.date,
    end: datetime.date,
    includes: str = RESERVATION_INCLUDES,
) -> list[dict]:
    """
    All reservations for the given properties within a check-in date window.

    properties[] is REQUIRED by the API — omitting it returns nothing.
    The date window must be explicit; the API default (next 2 weeks) is too narrow.

    Each record adds:
    - uuid   (str)       — normalized from 'id'
    - status (str|None)  — from reservation_status.current.category
    """
    params = _qs("properties[]", property_uuids) + [
        ("include", includes),
        ("start_date", start.isoformat()),
        ("end_date", end.isoformat()),
        ("date_query", "checkin"),
        ("per_page", "50"),
    ]
    items = client.get_pages("/reservations", params=params)
    return [
        {
            **r,
            "uuid": _uuid(r),
            "status": res_status(r),
        }
        for r in items
    ]


def get_reservation_messages(
    client: HospitableClient,
    reservation_uuid: str,
) -> list[dict]:
    """
    Messages for a single reservation, via the throttled client method.

    The messages endpoint is hard-limited to 2 req/min per reservation UUID
    (confirmed live). HospitableClient.get_messages() enforces this.

    Each message adds:
    - sender (str|None) — normalized from sender_type / sender / author
    """
    body = client.get_messages(reservation_uuid)
    messages = body.get("data", [])
    if not isinstance(messages, list):
        messages = [messages]
    return [
        {
            **m,
            "sender": msg_sender(m),
        }
        for m in messages
    ]


def get_inquiries(
    client: HospitableClient,
    property_uuids: list[str],
) -> list[dict]:
    """
    All inquiries for the given properties (summary level — no messages).

    properties[] is REQUIRED. Use get_inquiry_thread() to load the message
    thread for a specific inquiry.
    """
    params = _qs("properties[]", property_uuids) + [
        ("include", "financials,guest,properties,listings"),
        ("per_page", "50"),
    ]
    items = client.get_pages("/inquiries", params=params)
    return [
        {
            **i,
            "uuid": _uuid(i),
        }
        for i in items
    ]


def get_inquiry_thread(
    client: HospitableClient,
    inquiry_uuid: str,
) -> dict:
    """
    Inquiry detail with the full message thread.

    Returns the data object with messages[] normalized:
    - Each message has 'sender' (from sender_type / sender / author)
    - messages is always a list (the API may return a single dict for one-message threads)

    If the inquiry has been converted to a reservation, the API returns 410.
    Callers should handle requests.HTTPError with status 410.
    """
    body = client.get(
        f"/inquiries/{inquiry_uuid}",
        params={"include": "messages,guest"},
    )
    detail = body.get("data", {})

    raw_msgs = detail.get("messages")
    if isinstance(raw_msgs, dict):
        raw_msgs = [raw_msgs]
    if isinstance(raw_msgs, list):
        detail["messages"] = [
            {**m, "sender": msg_sender(m)}
            for m in raw_msgs
        ]

    return detail


def get_guest_reviews(
    client: HospitableClient,
    status: str = "all",
) -> list[dict]:
    """
    Guest reviews across all properties.

    status="all" returns pending + submitted + expired in one call.

    Key fields for the actionable-review check:
    - can_be_sent_now (bool) — direct signal that the review window is open
    - expires_at (str|None)  — deadline for pending reviews; None means the
      window hasn't opened yet (guest hasn't checked out, or platform hasn't
      surfaced a deadline). Confirm population before relying on date math.
    - status ("pending" | "submitted" | "expired")
    """
    items = client.get_pages(
        "/guest-reviews",
        params=[("status", status), ("per_page", "50")],
    )
    return [
        {
            **r,
            "uuid": _uuid(r),
        }
        for r in items
    ]


def get_knowledge_hub(
    client: HospitableClient,
    property_uuid: str,
) -> dict:
    """
    Knowledge hub for one property.

    Returns the 'data' object with shape:
      { property: {...}, sources: [...], topics: [...] }

    Each topic has aggregate_items[]; each item has:
      content, is_edited, state, updated_at

    Called per-property — there is no multi-property KH endpoint.
    """
    body = client.get(f"/properties/{property_uuid}/knowledge-hub")
    return body.get("data", {})


def get_tasks(
    client: HospitableClient,
    property_uuids: list[str],
    start: datetime.date,
    end: datetime.date,
) -> list[dict]:
    """
    Cleaning/turnover tasks for the given properties and date window.

    An empty list for a reservation window is the missing-turnover-task signal.
    properties[] is REQUIRED.
    """
    params = _qs("properties[]", property_uuids) + [
        ("start_date", start.isoformat()),
        ("end_date", end.isoformat()),
        ("per_page", "50"),
    ]
    items = client.get_pages("/tasks", params=params)
    return [
        {
            **t,
            "uuid": _uuid(t),
        }
        for t in items
    ]
