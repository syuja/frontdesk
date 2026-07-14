"""
Check: unanswered_inquiry  [HIGH]

Flag inquiries where the last message sender is a guest and the gap since
that message is within [inquiry_stale_hours, inquiry_max_age_hours].

Lower bound (inquiry_stale_hours, default 3h): ignore fresh gaps — the host
may still be composing a reply.

Upper bound (inquiry_max_age_hours, default 336h = 14 days): past ~2 weeks an
unanswered inquiry is no longer a recoverable booking. Surfacing it trains the
reader to ignore the digest; the one genuinely actionable item drowns in noise.

Non-actionable inquiries are skipped at two points:
- Pre-thread: inquiry.status in a set of known closed/terminal states.
- Post-fetch: 410 HTTP response means the inquiry converted to a reservation.

This is the only check that performs per-entity I/O: it calls
data.get_inquiry_thread() for each inquiry to inspect the message thread.
410 responses (inquiry converted to a reservation) are skipped gracefully.
The /inquiries/{uuid} endpoint is not subject to the 2-req/min reservation
message throttle — that limit applies only to /reservations/{uuid}/messages.
"""

from __future__ import annotations

import datetime
import logging

import requests

import hospitable.data as hdata
from checks._utils import extract_uuid, lookup_prop_name, parse_dt
from checks.finding import AuditData, CheckConfig, Finding, Severity

log = logging.getLogger(__name__)

# Inquiry statuses that are definitively closed — skip before fetching the thread.
# The 410-on-thread-fetch path covers "converted to reservation".
# These values are based on the Hospitable API inquiry status enum; the check
# falls through safely if the field is absent or has an unknown value.
_CLOSED_STATUSES = frozenset({
    "declined",
    "expired",
    "withdrawn",
    "cancelled",
    "closed",
})

# Sentinel: messages missing created_at sort to this so they can never be
# mistakenly treated as the newest message in the thread.
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _msg_ts(msg: dict) -> datetime.datetime:
    """Parse a message's timestamp for ordering. Missing → epoch (defensively oldest)."""
    raw = msg.get("created_at") or msg.get("sent_at") or ""
    return parse_dt(raw) or _EPOCH


def check_unanswered_inquiry(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    For each inquiry, load its message thread and flag if:
    - the last message sender is "guest"
    - the gap is >= config.inquiry_stale_hours (lower bound)
    - the gap is <= config.inquiry_max_age_hours (upper bound)

    Pre-filters:
    - inquiry.status in _CLOSED_STATUSES → skip before thread fetch
    - 410 on thread fetch → converted to reservation, skip
    """
    prop_index = {p["uuid"]: p for p in audit.props}
    stale_threshold = datetime.timedelta(hours=config.inquiry_stale_hours)
    max_age = datetime.timedelta(hours=config.inquiry_max_age_hours)
    findings: list[Finding] = []

    for inquiry in audit.inquiries:
        inq_uuid = inquiry.get("uuid") or ""
        if not inq_uuid:
            continue

        # Skip closed/terminal inquiries before making a network call
        status = inquiry.get("status")
        if status in _CLOSED_STATUSES:
            log.debug("Inquiry %s status=%s — skipping", inq_uuid[:8], status)
            continue

        prop_uuid = extract_uuid(inquiry, "property_uuid", "property_id", "property", "properties")
        pname = lookup_prop_name(prop_uuid, prop_index)

        try:
            thread = hdata.get_inquiry_thread(audit.client, inq_uuid)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 410:
                log.debug("Inquiry %s → 410 (converted to reservation), skipping", inq_uuid[:8])
                continue
            log.warning("Failed to load inquiry thread %s: %s", inq_uuid[:8], exc)
            continue
        except Exception as exc:
            log.warning("Unexpected error for inquiry %s: %s", inq_uuid[:8], exc)
            continue

        messages = thread.get("messages") or []
        if not messages:
            continue

        # Pick the newest message by timestamp — never trust API delivery order.
        # The Hospitable API returns messages newest-first, but an index assumption
        # ([-1] or [0]) breaks silently if that ever changes. max() is explicit.
        newest_msg = max(messages, key=_msg_ts)
        # msg_sender() normalization is applied in get_inquiry_thread()
        sender = newest_msg.get("sender")
        if sender != "guest":
            continue

        ts_str = newest_msg.get("created_at") or newest_msg.get("sent_at") or ""
        last_at = parse_dt(ts_str)
        if last_at is None:
            log.debug("Inquiry %s: no parseable timestamp on last message, skipping", inq_uuid[:8])
            continue

        gap = now - last_at

        # Lower bound: not yet stale
        if gap < stale_threshold:
            continue

        # Upper bound: too old to be a recoverable booking
        if gap > max_age:
            log.debug(
                "Inquiry %s gap=%.1fh exceeds max_age=%dh — skipping",
                inq_uuid[:8], gap.total_seconds() / 3600, config.inquiry_max_age_hours,
            )
            continue

        gap_h = gap.total_seconds() / 3600
        ages_out_h = config.inquiry_max_age_hours - gap_h
        severity = (
            Severity.CRITICAL
            if gap_h >= config.inquiry_escalate_hours
            else Severity.HIGH
        )

        guest_name = (thread.get("guest") or {}).get("first_name") or None
        title_guest = guest_name or "unknown guest"

        findings.append(Finding(
            check="unanswered_inquiry",
            severity=severity,
            property_uuid=prop_uuid,
            property_name=pname,
            title=f"Unanswered inquiry — {title_guest} last replied {gap_h:.1f}h ago",
            detail=(
                f"inquiry={inq_uuid[:8]} "
                f"last_message_at={ts_str} "
                f"gap={gap_h:.1f}h | ages out in {ages_out_h:.0f}h"
            ),
            entity_id=inq_uuid,
            guest_name=guest_name,
        ))

    return findings
