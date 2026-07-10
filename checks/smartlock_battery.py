"""
Check: smartlock_battery  [CRITICAL]

Flag smartlocks whose battery is low, unreadable, or whose device is offline.

Rationale: a dead or unreachable smartlock = guest locked out. CRITICAL is the
only appropriate severity — there is no non-urgent version of "guest can't enter."

Design principle — over-catch on UNCERTAINTY, never on EARLINESS:
  battery.status "low" at 49% is early, not urgent. The Schlage label fires at
  manufacturer calibration; a nightly check has far more lead time. Flagging on
  status alone would emit CRITICAL for ~15 nights before action is needed, and
  the reader would learn to skip it.
  → battery.status is CONTEXT ONLY, never a trigger.
  → When we genuinely cannot read the battery state (missing data, offline lock),
    we DO flag — that is uncertainty, not earliness.

Tripwires (any one fires CRITICAL):
  a) battery.percentage < battery.threshold    (device-configured threshold, currently 30%)
  b) battery.percentage < config.battery_floor_pct  (backstop if threshold absent/wrong)
  c) issues[] contains a low-battery entry     (platform has escalated beyond its own label)
  d) battery object or percentage missing/None (uncertainty — cannot read state)
  e) device.online is False OR state.online is False  (uncertainty — lock unreachable;
     cannot confirm a guest could enter)

ONE Finding per unique lock. audit.smartlocks is pre-deduped by build_audit_data.
Pure function: does no I/O, reads only audit.smartlocks.
"""

from __future__ import annotations

import datetime
import logging

from checks._utils import lookup_prop_name
from checks.finding import AuditData, CheckConfig, Finding, Severity

log = logging.getLogger(__name__)


def check_smartlock_battery(
    audit: AuditData,
    *,
    now: datetime.datetime,
    config: CheckConfig,
) -> list[Finding]:
    """
    Inspect each deduped smartlock for battery or connectivity issues.
    CRITICAL severity only — a dead or offline lock cannot admit a guest.
    """
    prop_index = {p["uuid"]: p for p in audit.props}
    findings: list[Finding] = []

    for lock in audit.smartlocks:
        if lock.get("device_type") != "smartlock":
            continue

        tripwires: list[str] = []

        state = lock.get("state") or {}
        battery = state.get("battery") if state else None
        dev_online = lock.get("online")
        state_online = state.get("online")

        # (e) offline — uncertainty: cannot confirm a guest could enter
        if dev_online is False or state_online is False:
            tripwires.append("lock offline — state unknown")

        if battery is None:
            # (d) battery object entirely missing
            tripwires.append("battery data missing")
        else:
            pct_raw = battery.get("percentage")
            threshold_raw = battery.get("threshold")

            if pct_raw is None:
                # (d) percentage field missing
                tripwires.append("battery percentage missing")
            else:
                try:
                    pct_f = float(pct_raw)
                    threshold_f = float(threshold_raw) if threshold_raw is not None else None

                    # (a) below device-configured threshold
                    if threshold_f is not None and pct_f < threshold_f:
                        tripwires.append(
                            f"percentage {pct_f:.0f}% < configured threshold {threshold_f:.0f}%"
                        )

                    # (b) below floor backstop (catches absent/misconfigured threshold)
                    if pct_f < config.battery_floor_pct:
                        tripwires.append(
                            f"percentage {pct_f:.0f}% < floor {config.battery_floor_pct}%"
                        )

                except (TypeError, ValueError):
                    tripwires.append(f"unparseable percentage {pct_raw!r}")

            # (c) platform-issued low-battery issues entry
            low_issues = [
                i for i in (lock.get("issues") or [])
                if isinstance(i, dict) and "battery" in str(i.get("type", "")).lower()
            ]
            if low_issues:
                n = len(low_issues)
                tripwires.append(
                    f"issues[] has {n} low-battery entr{'y' if n == 1 else 'ies'}"
                )

        if not tripwires:
            continue

        lock_id = lock.get("id") or ""
        lock_name = lock.get("name") or "unknown"
        prop_uuid = lock.get("_prop_uuid") or ""
        pname = lookup_prop_name(prop_uuid, prop_index)

        # Battery context line (context only — status is NOT a trigger)
        if battery is not None:
            pct_str = (
                f"{battery.get('percentage')}%"
                if battery.get("percentage") is not None else "missing"
            )
            thr_str = (
                f"{battery.get('threshold')}%"
                if battery.get("threshold") is not None else "missing"
            )
            batt_status = battery.get("status") or "unknown"
            batt_context = (
                f"battery={pct_str} (threshold={thr_str}, status={batt_status!r})"
            )
        else:
            batt_context = "battery=missing"

        online_str = (
            "OFFLINE" if (dev_online is False or state_online is False) else "online"
        )

        findings.append(Finding(
            check="smartlock_battery",
            severity=Severity.CRITICAL,
            property_uuid=prop_uuid,
            property_name=pname,
            title=f"Smartlock battery critical — {lock_name}",
            detail=(
                f"lock={lock_name} id={lock_id[:8]} "
                f"{batt_context} {online_str} | "
                f"tripwire: {'; '.join(tripwires)}"
            ),
            entity_id=lock_id,
        ))

    return findings
