"""
Orchestrator: fetches all data, runs all checks, returns sorted findings.
"""

from __future__ import annotations

import datetime
import logging

import hospitable.data as hdata
from hospitable.client import HospitableClient

from checks.finding import AuditData, CheckConfig, Finding
from checks.unanswered_inquiry import check_unanswered_inquiry
from checks.actionable_review import check_actionable_review
from checks.knowledge_hub_hygiene import check_knowledge_hub_hygiene
from checks.missing_turnover_task import check_missing_turnover_task

log = logging.getLogger(__name__)


def build_audit_data(
    client: HospitableClient,
    *,
    lookahead_days: int = 14,
    history_days: int = 365,
) -> AuditData:
    """
    Pull all data needed for one audit run.

    history_days: how far back to pull reservations (for review checks)
    lookahead_days: how far forward to pull reservations and tasks
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=history_days)
    end = today + datetime.timedelta(days=lookahead_days)

    log.info("Fetching properties…")
    props = hdata.get_properties(client)
    prop_uuids = [p["uuid"] for p in props]

    log.info("Fetching reservations (%s → %s)…", start, end)
    reservations = hdata.get_reservations(client, prop_uuids, start, end)

    log.info("Fetching inquiries…")
    inquiries = hdata.get_inquiries(client, prop_uuids)

    log.info("Fetching guest reviews…")
    reviews = hdata.get_guest_reviews(client)

    log.info("Fetching tasks (%s → %s)…", today, end)
    tasks = hdata.get_tasks(client, prop_uuids, today, end)

    log.info("Fetching knowledge hub per property…")
    kh: dict[str, dict] = {}
    for puuid in prop_uuids:
        kh[puuid] = hdata.get_knowledge_hub(client, puuid)

    return AuditData(
        props=props,
        inquiries=inquiries,
        reviews=reviews,
        reservations=reservations,
        tasks=tasks,
        kh=kh,
        client=client,
    )


def run_all(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    Run all 4 checks and return findings sorted by severity (high→low),
    then property name.
    """
    findings: list[Finding] = []
    findings.extend(check_unanswered_inquiry(audit, now=now, config=config))
    findings.extend(check_actionable_review(audit, now=now, config=config))
    findings.extend(check_knowledge_hub_hygiene(audit, now=now, config=config))
    findings.extend(check_missing_turnover_task(audit, now=now, config=config))
    return sorted(findings, key=lambda f: (-f.severity, f.property_name))
