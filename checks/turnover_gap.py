"""
Check: turnover_gap  [LOW–CRITICAL]

Surfaces upcoming turnovers and their cleaning window across the COMBINED
reservation calendar of all properties in the account.

Domain rationale: the 3 property UUIDs (parent "Ensuite and Queen" 4d22b785,
child Ensuite 249a5a68, child Queen 307872bb) share ONE physical space —
kitchen, bath, trash, common areas. Any checkout creates cleaning work before
any arrival, regardless of which room listing is involved. All reservations
are merged into a single combined calendar; room-to-room matching is not
attempted.

Gap tightness = cleaning urgency:
    gap == 0 (same-day)  →  CRITICAL  (no cleaning buffer at all)
    gap == 1 day         →  HIGH      (24h for turnover)
    gap in 2–3 days      →  MEDIUM    (some buffer, still tight)
    gap >= 4 days        →  LOW       (comfortable window)

Thresholds live in CheckConfig (turnover_gap_*_days). The lookahead window
(turnover_lookahead_days) controls how far ahead check-ins are surfaced.

For each upcoming check-in the check finds the most recent prior checkout
across the combined calendar. If no prior checkout exists (first arrival
after a vacancy), the check-in is silently skipped — there is no turnover.

Pure function: uses pre-fetched reservations, does no I/O.
"""

from __future__ import annotations

import datetime
import logging

import hospitable.data as hdata
from checks._utils import extract_uuid, lookup_prop_name, parse_date
from checks.finding import AuditData, CheckConfig, Finding, Severity

log = logging.getLogger(__name__)

# Deny-list: unknown/unrecognised statuses default to INCLUDED — a missed turnover is
# worse than a false positive. Flip this if a new terminal status appears in the API.
_SKIP_STATUSES = frozenset({"cancelled", "not_accepted", "declined"})


def _checkin_date(res: dict) -> datetime.date | None:
    for key in ("check_in", "checkin", "check_in_date", "checkin_date", "start_date"):
        d = parse_date(res.get(key))
        if d:
            return d
    return None


def _checkout_date(res: dict) -> datetime.date | None:
    for key in ("check_out", "checkout", "check_out_date", "checkout_date", "end_date"):
        d = parse_date(res.get(key))
        if d:
            return d
    return None


def _gap_severity(gap: int, config: CheckConfig) -> Severity:
    if gap <= config.turnover_gap_critical_days:
        return Severity.CRITICAL
    if gap <= config.turnover_gap_high_days:
        return Severity.HIGH
    if gap <= config.turnover_gap_medium_days:
        return Severity.MEDIUM
    return Severity.LOW


def _gap_label(gap: int) -> str:
    return "same-day" if gap == 0 else f"{gap}d gap"


def check_turnover_gap(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    For each upcoming check-in (within turnover_lookahead_days of now), find
    the most recent prior checkout across the combined property calendar and
    emit a Finding whose severity reflects the cleaning gap between the two.
    """
    prop_index = {p["uuid"]: p for p in audit.props}
    today = now.date()
    window_end = today + datetime.timedelta(days=config.turnover_lookahead_days)

    # TODO: scope to shared-space UUIDs when a non-shared property is added
    # Parse all reservations into structured tuples; skip only known-dead statuses
    events: list[tuple[datetime.date, datetime.date, str, str, str]] = []
    for res in audit.reservations:
        status = hdata.res_status(res) or res.get("status")
        if status in _SKIP_STATUSES:
            continue
        cin = _checkin_date(res)
        cout = _checkout_date(res)
        if cin is None or cout is None:
            continue
        prop_uuid = extract_uuid(res, "property_uuid", "property_id", "property", "properties")
        pname = lookup_prop_name(prop_uuid, prop_index)
        res_uuid = res.get("uuid") or ""
        events.append((cin, cout, res_uuid, prop_uuid, pname))

    # All checkouts across the combined calendar, sorted ascending for binary-search-style scan
    all_checkouts: list[tuple[datetime.date, str, str]] = sorted(
        [(cout, pname, uuid) for cin, cout, uuid, _puuid, pname in events],
        key=lambda x: x[0],
    )

    findings: list[Finding] = []

    for cin, _cout, uuid_in, prop_uuid_in, pname_in in events:
        if not (today <= cin <= window_end):
            continue

        # Walk backwards through checkouts to find the latest one on or before this check-in
        prior: tuple[datetime.date, str, str] | None = None
        for chk_date, chk_pname, chk_uuid in reversed(all_checkouts):
            if chk_uuid == uuid_in:
                continue  # skip this reservation's own checkout
            if chk_date <= cin:
                prior = (chk_date, chk_pname, chk_uuid)
                break

        if prior is None:
            log.debug(
                "Check-in %s on %s — no prior checkout found (first arrival), skipping",
                uuid_in[:8], cin,
            )
            continue

        prior_date, prior_pname, _prior_uuid = prior
        gap = (cin - prior_date).days
        severity = _gap_severity(gap, config)
        label = _gap_label(gap)

        findings.append(Finding(
            check="turnover_gap",
            severity=severity,
            property_uuid=prop_uuid_in,
            property_name=pname_in,
            title=f"Turnover gap {gap}d — {label}",
            detail=(
                f"OUT: {prior_pname} {prior_date}"
                f" → IN: {pname_in} {cin}"
                f" | gap={gap}d ({label})"
                f" | arriving={uuid_in[:8]}"
            ),
            entity_id=uuid_in,
        ))

    return findings
