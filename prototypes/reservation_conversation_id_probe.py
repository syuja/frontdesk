"""
Diagnostic probe: does get-reservations surface a conversation-level ID,
and what ID does get-reservation-messages actually accept?

Phase 1/2 docs (PHASE2_PLAN.md, PHASE3_PLAN.md) assert get-reservation-messages
needs an internal "conversation UUID" that get-reservations does not surface,
and that an ID cache was planned to bridge the two. CURRENT_STATUS.md later
says "ID cache: drop it — not needed", and hospitable/client.py's
get_messages() calls GET /reservations/{reservation_uuid}/messages using the
reservation's own uuid directly. This probe verifies live which claim holds:
prints the full raw get-reservations record for real reservations (every key,
unfiltered), then calls get-reservation-messages with that same reservation
uuid and confirms whether it returns a thread or errors.

Read-only, GET only. Does not modify checks/*.py or hospitable/data.py.

Run:
    uv run prototypes/reservation_conversation_id_probe.py

PII policy: sender/role and message counts printed; no guest names, emails,
phones, or message body text. PAT never printed.
"""
from __future__ import annotations

import datetime
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hospitable.client import HospitableClient
import hospitable.data as hdata

N_RESERVATIONS_TO_TEST = 5


def _looks_conversation_shaped(key: str) -> bool:
    key_l = key.lower()
    return any(tok in key_l for tok in ("conversation", "thread", "message", "chat"))


def main() -> None:
    client = HospitableClient()

    print("── Properties ──────────────────────────────────────────────────")
    props = hdata.get_properties(client)
    prop_uuids = [p["uuid"] for p in props]
    print(f"  total properties: {len(props)}")
    if not props:
        print("  No properties returned — aborting.")
        return

    print("\n── Reservations (raw, unfiltered) ─────────────────────────────")
    today = datetime.date.today()
    start = today - datetime.timedelta(days=90)
    end = today + datetime.timedelta(days=60)

    # Raw call, bypassing hdata.get_reservations()'s normalization, to see
    # exactly what the API returns before any local key-adding.
    raw_params = [("properties[]", u) for u in prop_uuids] + [
        ("include", hdata.RESERVATION_INCLUDES),
        ("start_date", start.isoformat()),
        ("end_date", end.isoformat()),
        ("date_query", "checkin"),
        ("per_page", "50"),
    ]
    raw_body = client.get("/reservations", params=raw_params)
    raw_items = raw_body.get("data", [])
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    print(f"  {start} → {end}: {len(raw_items)} reservations (raw response)")

    if not raw_items:
        print("  No reservations in window — aborting.")
        return

    sample = raw_items[0]
    print("\n  Full raw keys on reservation[0]:")
    for k in sorted(sample.keys()):
        v = sample[k]
        flag = "  <-- CONVERSATION-SHAPED?" if _looks_conversation_shaped(k) else ""
        print(f"    {k!r}: {type(v).__name__}{flag}")

    print("\n  Full raw JSON of reservation[0] (for shape inspection):")
    print(json.dumps(sample, indent=2, default=str))

    # Also check nested include objects (guest, listings, properties, financials,
    # review) for anything conversation-shaped, since it could be nested.
    print("\n  Scanning nested include objects for conversation-shaped keys...")
    found_any_nested = False
    for top_key, top_val in sample.items():
        candidates = top_val if isinstance(top_val, list) else [top_val]
        for c in candidates:
            if isinstance(c, dict):
                for k in c.keys():
                    if _looks_conversation_shaped(k):
                        found_any_nested = True
                        print(f"    {top_key}.{k} — CONVERSATION-SHAPED")
    if not found_any_nested:
        print("    none found")

    # ── Resolution path Phase 2 actually uses ──────────────────────────────
    print("\n── Resolution path per hdata.get_reservations() ──────────────")
    reservations = hdata.get_reservations(client, prop_uuids, start, end)
    test_set = reservations[:N_RESERVATIONS_TO_TEST]
    print(f"  Testing get-reservation-messages against {len(test_set)} reservation uuid(s)")
    print("  (uuid here = normalized 'id' field from get_reservations — the only")
    print("   id data.py exposes; no separate conversation-id lookup exists in data.py)")

    for r in test_set:
        res_uuid = r["uuid"]
        status = r.get("status")
        print(f"\n  reservation={res_uuid[:8]}  status={status}")
        try:
            msgs = hdata.get_reservation_messages(client, res_uuid)
        except Exception as exc:
            print(f"    get_reservation_messages FAILED: {type(exc).__name__}: {exc}")
            continue
        print(f"    get_reservation_messages OK — {len(msgs)} message(s)")
        if msgs:
            senders = [m.get("sender") for m in msgs]
            print(f"    senders present: {sorted(set(s for s in senders if s))}")

    print("\n── Probe complete ──────────────────────────────────────────────")


if __name__ == "__main__":
    main()
