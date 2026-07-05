"""
Check: missing_turnover_task  [LOW]

Flag active reservations whose checkout falls within
config.turnover_lookahead_days (default 14) and for which no task exists
in the pre-fetched task list for that property near the checkout date.

Phase 1 rationale: turnover tasks not set up for upcoming stays means
the cleaning crew may not be scheduled — a silent guest-facing failure.

Pure function: cross-references pre-fetched reservations and tasks, does
no I/O. Note: the account currently returns 0 tasks, so this check will
fire for all upcoming checkouts — expected behavior, not a bug.
"""

from __future__ import annotations

import datetime
import logging

from checks._utils import extract_uuid, lookup_prop_name, parse_date
from checks.finding import AuditData, CheckConfig, Finding, Severity

log = logging.getLogger(__name__)

# A task "covers" a checkout if its date falls within this window (days either side)
_TASK_MATCH_WINDOW_DAYS = 1

# Only flag reservations in these statuses — ignore enquiries, cancelled, etc.
_ACTIVE_STATUSES = frozenset({"accepted", "confirmed"})


def check_missing_turnover_task(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    For each accepted/confirmed reservation checking out within lookahead_days,
    flag it if no task exists for that property on or near the checkout date.
    """
    prop_index = {p["uuid"]: p for p in audit.props}
    today = now.date()
    lookahead_end = today + datetime.timedelta(days=config.turnover_lookahead_days)
    findings: list[Finding] = []

    # Index tasks by property UUID → set of task dates
    task_dates: dict[str, set[datetime.date]] = {}
    for task in audit.tasks:
        tuuid = extract_uuid(task, "property_uuid", "property_id", "property", "properties")
        tdate = _task_date(task)
        if tuuid and tdate:
            task_dates.setdefault(tuuid, set()).add(tdate)

    for res in audit.reservations:
        if res.get("status") not in _ACTIVE_STATUSES:
            continue

        checkout = _checkout_date(res)
        if checkout is None:
            continue
        if not (today <= checkout <= lookahead_end):
            continue

        prop_uuid = extract_uuid(res, "property_uuid", "property_id", "property", "properties")
        pname = lookup_prop_name(prop_uuid, prop_index)
        res_uuid = res.get("uuid") or ""

        dates_for_prop = task_dates.get(prop_uuid, set())
        has_task = any(
            abs((d - checkout).days) <= _TASK_MATCH_WINDOW_DAYS
            for d in dates_for_prop
        )
        if has_task:
            continue

        findings.append(Finding(
            check="missing_turnover_task",
            severity=Severity.LOW,
            property_uuid=prop_uuid,
            property_name=pname,
            title=f"No turnover task for checkout on {checkout}",
            detail=(
                f"reservation={res_uuid[:8]} "
                f"checkout={checkout} "
                f"tasks_for_property={len(dates_for_prop)} "
                f"lookahead={config.turnover_lookahead_days}d"
            ),
            entity_id=res_uuid,
        ))

    return findings


def _checkout_date(res: dict) -> datetime.date | None:
    for key in ("check_out", "checkout", "check_out_date", "checkout_date"):
        d = parse_date(res.get(key))
        if d:
            return d
    return None


def _task_date(task: dict) -> datetime.date | None:
    for key in ("start_date", "end_date", "scheduled_for", "date", "due_date", "scheduled_at"):
        d = parse_date(task.get(key))
        if d:
            return d
    return None
