"""
Check: knowledge_hub_hygiene  [MEDIUM]

Two sub-findings per property:

a) STALE — any aggregate_item whose updated_at is older than
   config.kh_stale_days (default 180 days). One Finding per stale item.

b) DUPLICATE — any topic name appearing more than once on the same property.
   (Confirmed present: parent 4d22b785 has two "Parking" topics.)
   One Finding per duplicate topic name.

Phase 1 rationale: seven stale entries found by hand-audit; parking info
was absent as the #1 guest question because it was buried under a duplicate.

Pure function: takes pre-fetched kh dict, does no I/O.
"""

from __future__ import annotations

import datetime
import logging
from collections import Counter

from checks._utils import lookup_prop_name, parse_dt
from checks.finding import AuditData, CheckConfig, Finding, Severity

log = logging.getLogger(__name__)


def check_knowledge_hub_hygiene(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    For each property's knowledge hub:
    - Flag any item not updated within config.kh_stale_days.
    - Flag any topic name that appears more than once.
    """
    prop_index = {p["uuid"]: p for p in audit.props}
    stale_cutoff = now - datetime.timedelta(days=config.kh_stale_days)
    findings: list[Finding] = []

    for prop_uuid, kh_data in audit.kh.items():
        pname = lookup_prop_name(prop_uuid, prop_index)
        topics = kh_data.get("topics") or []

        # ── Duplicate topic names ──────────────────────────────────────────
        name_counts: Counter[str] = Counter(
            t.get("name", "") for t in topics if t.get("name")
        )
        for tname, count in name_counts.items():
            if count > 1:
                findings.append(Finding(
                    check="knowledge_hub_hygiene",
                    severity=Severity.MEDIUM,
                    property_uuid=prop_uuid,
                    property_name=pname,
                    title=f'Duplicate KH topic "{tname}" ({count}×)',
                    detail=(
                        f'topic="{tname}" appears {count} times on this property — '
                        "consolidate to prevent guest confusion"
                    ),
                    entity_id=prop_uuid,
                ))

        # ── Stale items ────────────────────────────────────────────────────
        for topic in topics:
            tname = topic.get("name") or "?"
            for item in topic.get("aggregate_items") or []:
                upd_str = item.get("updated_at")
                if not upd_str:
                    continue
                upd = parse_dt(upd_str)
                if upd is None:
                    continue
                if upd >= stale_cutoff:
                    continue
                age_days = (now - upd).days
                snippet = str(item.get("content") or "")[:60]
                findings.append(Finding(
                    check="knowledge_hub_hygiene",
                    severity=Severity.MEDIUM,
                    property_uuid=prop_uuid,
                    property_name=pname,
                    title=f'Stale KH item in "{tname}" ({age_days}d old)',
                    detail=(
                        f'topic="{tname}" '
                        f"updated={upd_str} "
                        f"age={age_days}d "
                        f"threshold={config.kh_stale_days}d "
                        f"content={snippet!r}"
                    ),
                    entity_id=prop_uuid,
                ))

    return findings
