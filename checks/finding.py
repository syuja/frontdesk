"""
Shared contract for all audit checks.

Finding        — frozen dataclass; uniform shape every check returns
Severity       — IntEnum so findings sort high→low with -f.severity
CheckConfig    — all thresholds in one place with documented defaults
AuditData      — pre-pulled data bundle; runner fetches once, checks consume
"""

from __future__ import annotations

import dataclasses
from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    """Ordered LOW → CRITICAL so -severity sorts highest first."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    def __str__(self) -> str:
        return self.name


@dataclasses.dataclass(frozen=True)
class Finding:
    """One audit finding. Immutable so callers can't mutate after creation."""
    check: str            # stable snake_case id, e.g. "unanswered_inquiry"
    severity: Severity
    property_uuid: str
    property_name: str
    title: str            # one-line human summary
    detail: str           # specifics: ids, dates, ages
    entity_id: str | None = None  # reservation/review/inquiry/topic uuid


@dataclasses.dataclass
class CheckConfig:
    # unanswered_inquiry: flag if guest is last sender and gap >= this
    inquiry_stale_hours: int = 3
    # unanswered_inquiry: skip if gap > this — past ~2 weeks an unanswered inquiry
    # is no longer a recoverable booking; surfacing it trains the reader to ignore
    # the digest. 336h = 14 days.
    inquiry_max_age_hours: int = 336

    # actionable_review: Airbnb's post-checkout window for submitting a guest review.
    # If expires_at is present, that date is used directly. If absent, the deadline is
    # estimated as checkout + review_window_days. Reviews past their computed deadline
    # are skipped — can_be_sent_now/pending alone overstate actionability.
    review_window_days: int = 14

    # turnover_gap: how far ahead to scan for upcoming check-ins
    turnover_lookahead_days: int = 7
    # turnover_gap: severity thresholds — gap in whole days between the prior checkout
    # and the next check-in across the combined property calendar.
    # gap <= critical_days → CRITICAL; <= high_days → HIGH; <= medium_days → MEDIUM; else LOW
    turnover_gap_critical_days: int = 0  # same-day: zero cleaning buffer
    turnover_gap_high_days: int = 1      # one day
    turnover_gap_medium_days: int = 3    # two or three days


@dataclasses.dataclass
class AuditData:
    """
    All pre-fetched data for one audit run.

    The runner builds this once; each check reads the fields it needs.
    client is typed Any to avoid a runtime circular import — it is always a
    HospitableClient and is only used by check_unanswered_inquiry.
    """
    props: list[dict]
    inquiries: list[dict]       # summary list; thread fetched per-item in check 1
    reviews: list[dict]
    reservations: list[dict]
    kh: dict[str, dict]         # property_uuid → knowledge hub data object
    client: Any                 # HospitableClient; None-safe in pure-data tests
