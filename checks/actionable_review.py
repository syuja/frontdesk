"""
Check: actionable_review  [HIGH]

Flag pending guest reviews where the review window is confirmed still open.

Phase 1 rationale: the review window for a problem guest almost expired
unnoticed — a 14-day platform deadline with no alerting.

Pure function: takes pre-fetched review list, does no I/O.

Window bound (what makes the finding actionable):
  - If expires_at IS present: trust it — flag only if expires_at > today.
  - If expires_at is absent: fall back to checkout + review_window_days and
    flag only if that computed deadline is still in the future.
  - If neither is available: skip conservatively.

Rationale for the bound: can_be_sent_now=True and status=pending remain set
long after the Airbnb window closes (confirmed: 10 of 11 live findings had
checkouts 48–273 days ago with no expires_at). Surfacing those trains the
reader to ignore the digest. The window bound is what makes a finding a real,
recoverable action.
"""

from __future__ import annotations

import datetime
import logging

from checks._utils import extract_uuid, lookup_prop_name, parse_date
from checks.finding import AuditData, CheckConfig, Finding, Severity

log = logging.getLogger(__name__)


def check_actionable_review(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    Filter reviews to status="pending" AND can_be_sent_now=True, then verify
    the review window is still open before flagging.

    Window bound (primary: expires_at; fallback: checkout + review_window_days).
    If neither is available the review is skipped conservatively.
    """
    prop_index = {p["uuid"]: p for p in audit.props}
    today = now.date()
    window = datetime.timedelta(days=config.review_window_days)
    findings: list[Finding] = []

    for review in audit.reviews:
        if review.get("status") != "pending":
            continue
        if not review.get("can_be_sent_now"):
            continue

        review_uuid = review.get("uuid") or ""
        prop_uuid = extract_uuid(review, "property_uuid", "property_id", "property", "properties")
        pname = lookup_prop_name(prop_uuid, prop_index)
        checkout = _checkout_date(review)
        days_since = (today - checkout).days if checkout else None

        expires_str = review.get("expires_at")

        if expires_str:
            # Platform surfaced an explicit deadline — trust it exclusively
            expires = parse_date(expires_str)
            if expires is None or expires <= today:
                log.debug("Review %s expires_at=%s is past — skipping", review_uuid[:8], expires_str)
                continue
            days_left = (expires - today).days
            bound_detail = f"expires={expires} ({days_left}d remaining)"
        elif checkout is not None:
            # Fallback: estimate deadline from checkout date
            deadline = checkout + window
            if deadline <= today:
                log.debug(
                    "Review %s checkout+%dd=%s is past — skipping",
                    review_uuid[:8], config.review_window_days, deadline,
                )
                continue
            days_left = (deadline - today).days
            bound_detail = (
                f"window=checkout+{config.review_window_days}d"
                f" → {deadline} ({days_left}d remaining)"
            )
        else:
            # No anchor to verify the window — skip conservatively
            log.debug("Review %s: no expires_at and no checkout date — skipping", review_uuid[:8])
            continue

        parts = [f"review={review_uuid[:8]}"]
        if checkout:
            parts.append(f"checkout={checkout} ({days_since}d ago)")
        parts.append(bound_detail)

        findings.append(Finding(
            check="actionable_review",
            severity=Severity.HIGH,
            property_uuid=prop_uuid,
            property_name=pname,
            title="Actionable review — pending and window is open now",
            detail=" | ".join(parts),
            entity_id=review_uuid,
        ))

    return findings


def _checkout_date(review: dict) -> datetime.date | None:
    """Extract checkout date from a review record across possible field shapes."""
    for key in ("check_out", "checkout", "check_out_date", "checkout_date"):
        d = parse_date(review.get(key))
        if d:
            return d
    res = review.get("reservation") or {}
    for key in ("check_out", "checkout", "check_out_date"):
        d = parse_date(res.get(key))
        if d:
            return d
    return None
