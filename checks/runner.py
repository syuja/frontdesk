"""
Orchestrator: fetches all data, runs all checks, returns sorted findings.
"""

from __future__ import annotations

import datetime
import logging

import hospitable.data as hdata
from hospitable.client import HospitableClient

from checks.finding import AuditData, CheckConfig, Finding, LockStatus, PropertyOccupancy
from checks._utils import extract_uuid, parse_date
from checks.unanswered_inquiry import check_unanswered_inquiry
from checks.actionable_review import check_actionable_review
from checks.knowledge_hub_hygiene import check_knowledge_hub_hygiene
from checks.turnover_gap import check_turnover_gap
from checks.smartlock_battery import check_smartlock_battery

log = logging.getLogger(__name__)


def _res_cin(res: dict) -> datetime.date | None:
    for k in ("check_in", "checkin", "check_in_date", "checkin_date", "start_date"):
        d = parse_date(res.get(k))
        if d:
            return d
    return None


def _res_cout(res: dict) -> datetime.date | None:
    for k in ("check_out", "checkout", "check_out_date", "checkout_date", "end_date"):
        d = parse_date(res.get(k))
        if d:
            return d
    return None


def build_occupancy_view(
    audit: AuditData,
    now: datetime.datetime,
) -> list[PropertyOccupancy]:
    """Build a per-property occupancy snapshot from pre-fetched reservation data.

    For each property: resolves the current guest (checkin <= today < checkout),
    their checkout date and stay length, or — if vacant — the next upcoming checkin.
    Whole-unit vs room distinction comes from the property's parent_child field.
    """
    today = now.date()

    by_prop: dict[str, list[dict]] = {p["uuid"]: [] for p in audit.props}
    for res in audit.reservations:
        if hdata.res_status(res) != "accepted":
            continue
        cin = _res_cin(res)
        cout = _res_cout(res)
        if cin is None or cout is None:
            continue
        prop_uuid = extract_uuid(res, "property_uuid", "property_id", "property", "properties")
        if prop_uuid in by_prop:
            by_prop[prop_uuid].append(res)

    result: list[PropertyOccupancy] = []
    for prop in audit.props:
        puuid = prop["uuid"]
        pname = prop.get("name") or puuid
        is_whole_unit = (prop.get("parent_child") or {}).get("type") != "child"

        current: dict | None = None
        for res in by_prop.get(puuid, []):
            cin = _res_cin(res)
            cout = _res_cout(res)
            if cin is not None and cout is not None and cin <= today <= cout:
                current = res
                break

        if current is not None:
            cin = _res_cin(current)
            cout = _res_cout(current)
            guest_name = (current.get("guest") or {}).get("first_name") or None
            nights = (cout - cin).days if cin and cout else None
            result.append(PropertyOccupancy(
                property_uuid=puuid,
                property_name=pname,
                is_whole_unit=is_whole_unit,
                guest_name=guest_name,
                checkout_date=cout,
                nights=nights,
                next_checkin=None,
            ))
        else:
            upcoming = [
                _res_cin(res) for res in by_prop.get(puuid, [])
                if _res_cin(res) is not None and _res_cin(res) > today
            ]
            result.append(PropertyOccupancy(
                property_uuid=puuid,
                property_name=pname,
                is_whole_unit=is_whole_unit,
                guest_name=None,
                checkout_date=None,
                nights=None,
                next_checkin=min(upcoming) if upcoming else None,
            ))

    return result


def _dedup_smartlocks(raw: list[dict]) -> list[dict]:
    """
    Deduplicate smartlocks by device id, keeping the worst reading per device.

    Confirmed necessary: one physical lock appears under all 3 property listings.

    Worst = lowest battery.percentage (None counts as worst-case); UNION of
    issues[] across all readings; offline if any listing reports offline.
    """
    groups: dict[str, list[dict]] = {}
    for dev in raw:
        dev_id = dev.get("id") or ""
        if not dev_id:
            continue
        groups.setdefault(dev_id, []).append(dev)

    result: list[dict] = []
    for readings in groups.values():
        if len(readings) == 1:
            result.append(dict(readings[0]))
            continue

        def _pct(r: dict) -> float:
            p = ((r.get("state") or {}).get("battery") or {}).get("percentage")
            return float(p) if p is not None else float("-inf")

        worst = min(readings, key=_pct)
        merged = dict(worst)
        merged["issues"] = list(worst.get("issues") or [])

        seen_issues = {str(i) for i in merged["issues"]}
        for r in readings:
            if r is worst:
                continue
            for issue in (r.get("issues") or []):
                s = str(issue)
                if s not in seen_issues:
                    merged["issues"].append(issue)
                    seen_issues.add(s)

        if any(
            r.get("online") is False or (r.get("state") or {}).get("online") is False
            for r in readings
        ):
            merged["online"] = False
            state = dict(merged.get("state") or {})
            state["online"] = False
            merged["state"] = state

        result.append(merged)

    return result


def _build_lock_statuses(smartlocks: list[dict]) -> list[LockStatus]:
    """Build typed LockStatus objects from the already-deduped lock dicts."""
    statuses: list[LockStatus] = []
    for lock in smartlocks:
        if lock.get("device_type") != "smartlock":
            continue
        state = lock.get("state") or {}
        offline = lock.get("online") is False or state.get("online") is False
        pct: float | None = None
        threshold: float | None = None
        try:
            battery = state.get("battery") or {}
            pct = float(battery.get("percentage"))
            threshold_raw = battery.get("threshold")
            threshold = float(threshold_raw) if threshold_raw is not None else None
        except (TypeError, ValueError):
            pass
        statuses.append(LockStatus(
            name=lock.get("name") or "unknown",
            pct=pct,
            threshold=threshold,
            offline=offline,
        ))
    return statuses


def build_audit_data(
    client: HospitableClient,
    *,
    lookahead_days: int = 14,
    history_days: int = 365,
) -> AuditData:
    """
    Pull all data needed for one audit run.

    history_days: how far back to pull reservations (for review checks)
    lookahead_days: how far forward to pull reservations
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

    log.info("Fetching knowledge hub per property…")
    kh: dict[str, dict] = {}
    for puuid in prop_uuids:
        kh[puuid] = hdata.get_knowledge_hub(client, puuid)

    log.info("Fetching smartlock devices…")
    raw_locks: list[dict] = []
    for prop in props:
        for dev in hdata.get_property_devices(client, prop["uuid"]):
            if dev.get("device_type") == "smartlock":
                raw_locks.append({**dev, "_prop_uuid": prop["uuid"]})
    smartlocks = _dedup_smartlocks(raw_locks)
    log.info(
        "  %d unique smartlock(s) after dedup (%d raw record(s))",
        len(smartlocks), len(raw_locks),
    )

    return AuditData(
        props=props,
        inquiries=inquiries,
        reviews=reviews,
        reservations=reservations,
        kh=kh,
        client=client,
        smartlocks=smartlocks,
        lock_statuses=_build_lock_statuses(smartlocks),
    )


def run_all(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    Run all checks and return findings sorted by severity (high→low),
    then property name.
    """
    findings: list[Finding] = []
    findings.extend(check_smartlock_battery(audit, now=now, config=config))
    findings.extend(check_unanswered_inquiry(audit, now=now, config=config))
    findings.extend(check_actionable_review(audit, now=now, config=config))
    findings.extend(check_knowledge_hub_hygiene(audit, now=now, config=config))
    findings.extend(check_turnover_gap(audit, now=now, config=config))
    return sorted(findings, key=lambda f: (-f.severity, f.property_name))
