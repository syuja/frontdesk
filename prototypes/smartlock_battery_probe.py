"""
THROWAWAY PROBE — do not import from production code.

Explores GET /properties/{uuid}/devices to map what the Hospitable API
actually returns for smartlock battery state. Prints raw structure and
evaluates whether each lock WOULD flag in a real check.

Run:
    uv run python prototypes/smartlock_battery_probe.py

Findings from this run should inform a real checks/smartlock_battery.py
check — or confirm the endpoint is blocked (403 / missing entitlement).
"""

from __future__ import annotations

import sys
import json
import logging

import requests

# Reuse production auth + HTTP layer — never re-implement.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from hospitable.client import HospitableClient
import hospitable.data as hdata

# ── Constants ─────────────────────────────────────────────────────────────────

# Minimum acceptable battery percentage. Below this a dead lock = guest lockout.
# In a real check this would be CRITICAL severity.
BATTERY_MIN_PCT = 20

logging.basicConfig(
    level=logging.WARNING,  # suppress client debug noise; probe prints its own output
    format="%(levelname)s  %(message)s",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(val: object, *keys: str, default: str = "MISSING") -> object:
    """Safely walk a nested dict; return default if any key is absent or None."""
    cur = val
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _fmt(val: object) -> str:
    return "MISSING" if val == "MISSING" or val is None else str(val)


def _would_flag(device: dict) -> tuple[bool, str]:
    """
    Evaluate whether this smartlock WOULD fire a CRITICAL finding.

    Two independent signals:
      1. battery.percentage < BATTERY_MIN_PCT
      2. issues[] contains a low-battery entry

    Returns (flag: bool, reason: str).
    """
    state = device.get("state") or {}
    battery = state.get("battery") or {}

    reasons: list[str] = []

    pct = battery.get("percentage")
    if pct is not None:
        try:
            if float(pct) < BATTERY_MIN_PCT:
                reasons.append(f"percentage {pct}% < {BATTERY_MIN_PCT}%")
        except (TypeError, ValueError):
            pass

    issues = device.get("issues") or []
    low_batt_issues = [
        i for i in issues
        if isinstance(i, dict) and "battery" in str(i.get("type", "")).lower()
    ]
    if low_batt_issues:
        reasons.append(f"issues[] has {len(low_batt_issues)} low-battery entry/entries")

    if reasons:
        return True, "; ".join(reasons)
    return False, "ok"


def probe_property(client: HospitableClient, prop_uuid: str, prop_name: str) -> dict:
    """
    Fetch devices for one property. Returns a summary dict.
    Handles 403 (missing scope/entitlement) gracefully.
    """
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  Property: {prop_name}  ({prop_uuid[:8]}…)")
    print(sep)

    try:
        body = client.get(f"/properties/{prop_uuid}/devices")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 403:
            print("  ✗ 403 Forbidden — likely missing `devices:read` scope or")
            print("    the `smart-devices` account entitlement.")
            print("    Cannot continue for this property.")
            return {"prop_uuid": prop_uuid, "status": 403, "devices": []}
        raise

    status = 200
    devices = body.get("data") or []
    if isinstance(devices, dict):
        devices = [devices]

    print(f"  ✓ 200 OK — {len(devices)} device(s) returned")

    if not devices:
        print("  (no devices on this property)")
        return {"prop_uuid": prop_uuid, "status": status, "devices": []}

    # Print raw top-level structure of first device for field mapping
    print("\n  [raw keys on first device record]")
    print("  ", list(devices[0].keys()))

    summaries: list[dict] = []
    smartlock_count = 0

    for dev in devices:
        dtype = _get(dev, "device_type", default="MISSING")
        is_lock = isinstance(dtype, str) and "lock" in dtype.lower()

        print(f"\n  ── Device: {_fmt(_get(dev, 'name'))} ──")
        print(f"     id           : {_fmt(_get(dev, 'id'))}")
        print(f"     device_type  : {_fmt(dtype)}")
        print(f"     manufacturer : {_fmt(_get(dev, 'manufacturer'))}")
        print(f"     integration  : {_fmt(_get(dev, 'integration'))}")
        print(f"     online       : {_fmt(_get(dev, 'online'))}")

        if is_lock:
            smartlock_count += 1
            state = dev.get("state") or {}
            battery = state.get("battery") or {}
            issues = dev.get("issues") or []

            print(f"     [smartlock battery]")
            print(f"       state.battery.status     : {_fmt(battery.get('status'))}")
            print(f"       state.battery.percentage : {_fmt(battery.get('percentage'))}")
            print(f"       state.battery.threshold  : {_fmt(battery.get('threshold'))}")

            if issues:
                print(f"       issues[] ({len(issues)} entries):")
                for iss in issues:
                    print(f"         {json.dumps(iss)}")
            else:
                print(f"       issues[]              : (empty)")

            # Print full state blob for field discovery
            print(f"     [full state blob]")
            print(f"       {json.dumps(state, indent=6)}")

            flag, reason = _would_flag(dev)
            verdict = f"WOULD FLAG [CRITICAL] — {reason}" if flag else "ok"
            print(f"     >> battery verdict    : {verdict}")

            summaries.append({
                "name": _fmt(_get(dev, "name")),
                "pct": _fmt(battery.get("percentage")),
                "would_flag": flag,
                "reason": reason,
            })
        else:
            print(f"     (not a smartlock — battery fields not applicable)")

    return {
        "prop_uuid": prop_uuid,
        "status": status,
        "device_count": len(devices),
        "smartlock_count": smartlock_count,
        "smartlocks": summaries,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    client = HospitableClient()

    print("=" * 60)
    print("  SMARTLOCK BATTERY PROBE")
    print(f"  BATTERY_MIN_PCT threshold = {BATTERY_MIN_PCT}%")
    print("=" * 60)

    props = hdata.get_properties(client)
    print(f"\n  {len(props)} properties loaded")

    results: list[dict] = []
    for p in props:
        result = probe_property(client, p["uuid"], p.get("name") or p["uuid"][:8])
        results.append(result)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    for r in results:
        puuid = r["prop_uuid"][:8]
        if r["status"] == 403:
            print(f"  {puuid}…  endpoint BLOCKED (403)")
            continue

        dc = r.get("device_count", 0)
        sc = r.get("smartlock_count", 0)
        print(f"  {puuid}…  {dc} device(s), {sc} smartlock(s)")
        for s in r.get("smartlocks", []):
            verdict = f"WOULD FLAG [CRITICAL]" if s["would_flag"] else "ok"
            print(f"    {s['name']:30s}  battery={s['pct']}%  {verdict}")
            if s["would_flag"]:
                print(f"      reason: {s['reason']}")

    all_blocked = all(r["status"] == 403 for r in results)
    any_data = any(r.get("smartlock_count", 0) > 0 for r in results)

    print()
    if all_blocked:
        print("  CONCLUSION: endpoint blocked on all properties.")
        print("  Action needed: enable `smart-devices` entitlement or add")
        print("  `devices:read` scope to the PAT before building a real check.")
    elif not any_data:
        print("  CONCLUSION: endpoint accessible but no smartlocks found.")
        print("  Verify devices are paired in the Hospitable account.")
    else:
        flagged = sum(
            1 for r in results
            for s in r.get("smartlocks", [])
            if s["would_flag"]
        )
        total_locks = sum(r.get("smartlock_count", 0) for r in results)
        print(f"  CONCLUSION: {total_locks} smartlock(s) found, "
              f"{flagged} would flag at {BATTERY_MIN_PCT}% threshold.")
        if flagged:
            print("  Battery data IS present — viable to build checks/smartlock_battery.py.")
        else:
            print("  All batteries above threshold. Data present → viable to build real check.")


if __name__ == "__main__":
    main()
