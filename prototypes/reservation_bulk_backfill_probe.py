"""
THROWAWAY PROBE — Session 0 Q2: does bulk/historical thread access work?

Not production ingestion code: no resumability, no idempotency, no
production error handling. Disposable script, kept only for its output
data and findings (see logs/session0_q2_cache.json, gitignored).

Builds on prototypes/reservation_conversation_id_probe.py's confirmed finding
(small-scale, ~5 recent reservations): get-reservation-messages takes the
reservation's OWN uuid directly — no separate "conversation UUID" resolution
exists in hospitable/data.py, and none is needed. This probe reuses that same
path (hdata.get_reservation_messages) unmodified, at bulk/historical scale:

1. Pull the FULL reservation history via hdata.get_reservations() (reused
   as-is) across a wide date window, oldest-first sample of ~20.
2. For each: fetch its message thread via hdata.get_reservation_messages(),
   recording success / empty / error separately.
3. Test whether GET /reservations/{uuid}/messages actually paginates (the
   OpenAPI spec shows no page/per_page/meta on this endpoint, unlike
   /inquiries or /guest-reviews — confirm empirically rather than trust that).
4. Instrument: total HTTP request count, wall time, any 429s encountered.
5. Cache everything (reservation list, per-thread raw messages, metrics) to
   logs/session0_q2_cache.json — reused as free input to Q1's corpus count.

Read-only, GET only. Does not modify checks/*.py or hospitable/data.py.

Run:
    uv run python prototypes/reservation_bulk_backfill_probe.py

PII policy: console output prints sender/role, message counts, and short
non-content metadata only — no guest names, emails, phones, or message body
text. The cache file DOES contain raw message bodies (needed for Q1's token
count) but lives under logs/, which is gitignored — never committed.
PAT never printed.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hospitable.client import HospitableClient
import hospitable.data as hdata

CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "session0_q2_cache.json")
SAMPLE_SIZE = 20
FULL_HISTORY_START = datetime.date(2010, 1, 1)  # deliberately earlier than any plausible account start


# ── Request/throttle instrumentation ────────────────────────────────────────

class _Instrumentation:
    def __init__(self) -> None:
        self.request_count = 0
        self.requests_log: list[dict] = []
        self.throttle_429_count = 0
        self.retry_5xx_count = 0
        self.start_monotonic = time.monotonic()

    def wrap_session(self, session) -> None:
        orig_get = session.get

        def counted_get(url, params=None, **kwargs):
            self.request_count += 1
            t = time.monotonic() - self.start_monotonic
            self.requests_log.append({"t": round(t, 2), "url": url, "params": params})
            return orig_get(url, params=params, **kwargs)

        session.get = counted_get

    def wall_time(self) -> float:
        return time.monotonic() - self.start_monotonic


class _ThrottleLogHandler(logging.Handler):
    """Counts 429 and 5xx retry warnings emitted by hospitable.client."""

    def __init__(self, inst: _Instrumentation) -> None:
        super().__init__(level=logging.WARNING)
        self.inst = inst

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "429" in msg:
            self.inst.throttle_429_count += 1
        elif "retrying in" in msg:
            self.inst.retry_5xx_count += 1


# ── Sort key (mirrors checks/turnover_gap.py's fallback list) ──────────────

def _checkin_date(res: dict) -> datetime.date | None:
    for key in ("check_in", "checkin", "check_in_date", "checkin_date", "start_date"):
        raw = res.get(key)
        if not raw:
            continue
        try:
            return datetime.date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
    return None


def main() -> None:
    inst = _Instrumentation()
    logging.getLogger("hospitable.client").addHandler(_ThrottleLogHandler(inst))
    logging.getLogger("hospitable.client").setLevel(logging.WARNING)

    client = HospitableClient()
    inst.wrap_session(client._session)

    print("── Properties ──────────────────────────────────────────────────")
    props = hdata.get_properties(client)
    prop_uuids = [p["uuid"] for p in props]
    print(f"  {len(props)} properties: {[u[:8] for u in prop_uuids]}")

    print("\n── Full reservation history (reused hdata.get_reservations) ──────")
    today = datetime.date.today()
    all_res = hdata.get_reservations(client, prop_uuids, FULL_HISTORY_START, today)
    print(f"  window {FULL_HISTORY_START} → {today}: {len(all_res)} reservations total")

    dated = [(r, _checkin_date(r)) for r in all_res]
    dated = [(r, d) for r, d in dated if d is not None]
    dropped = len(all_res) - len(dated)
    if dropped:
        print(f"  WARNING: {dropped} reservation(s) had no parseable check-in date — excluded from sort")

    dated.sort(key=lambda pair: pair[1])
    if dated:
        earliest = dated[0][1]
        latest = dated[-1][1]
        print(f"  actual discovered range: {earliest} → {latest}")
    else:
        print("  No reservations with a parseable check-in date found — aborting.")
        return

    sample = dated[:SAMPLE_SIZE]
    print(f"\n  Oldest-first sample: {len(sample)} reservations "
          f"({sample[0][1]} .. {sample[-1][1]})")

    print("\n── Per-reservation thread fetch (oldest → newest) ────────────────")
    results: list[dict] = []
    for i, (res, cin) in enumerate(sample):
        res_uuid = res["uuid"]
        status = res.get("status")
        print(f"\n  [{i}] {res_uuid[:8]}  check_in={cin}  status={status!r}")

        entry: dict = {
            "index": i,
            "reservation_uuid": res_uuid,
            "check_in": cin.isoformat(),
            "status": status,
        }

        t0 = time.monotonic()
        try:
            msgs = hdata.get_reservation_messages(client, res_uuid)
        except Exception as exc:  # noqa: BLE001 — throwaway probe, want to see everything
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            entry["result"] = "error"
            entry["error_type"] = type(exc).__name__
            entry["error_status_code"] = status_code
            entry["error_detail"] = str(exc)[:200]
            entry["fetch_seconds"] = round(time.monotonic() - t0, 2)
            print(f"    ERROR  {type(exc).__name__} (HTTP {status_code}): {str(exc)[:150]}")
            results.append(entry)
            continue

        fetch_s = round(time.monotonic() - t0, 2)
        entry["fetch_seconds"] = fetch_s
        entry["message_count"] = len(msgs)
        entry["messages_raw"] = msgs  # cached for Q1 reuse; not printed to console
        senders = sorted({m.get("sender") for m in msgs if m.get("sender")})
        entry["senders_seen"] = senders

        if msgs:
            entry["result"] = "success"
            print(f"    OK — {len(msgs)} message(s) in {fetch_s}s, senders={senders}")
        else:
            entry["result"] = "empty"
            print(f"    OK but EMPTY — 0 messages ({fetch_s}s)")

        results.append(entry)

    # ── Pagination behavior test ────────────────────────────────────────────
    print("\n── Pagination behavior test (GET /reservations/{uuid}/messages) ──")
    successful = [e for e in results if e["result"] == "success"]
    pagination_report: dict = {"tested": False}

    if not successful:
        print("  No successful thread fetch in the oldest-20 sample — nothing to test.")
    else:
        longest = max(successful, key=lambda e: e["message_count"])
        print(f"  Longest thread in sample: {longest['reservation_uuid'][:8]} "
              f"({longest['message_count']} messages) — using it to test pagination params")

        uuid = longest["reservation_uuid"]
        page1 = client.get(f"/reservations/{uuid}/messages", params={"page": 1, "per_page": 1})
        time.sleep(31)  # respect the documented 2 req/min per-reservation throttle
        page2 = client.get(f"/reservations/{uuid}/messages", params={"page": 2, "per_page": 1})

        d1 = page1.get("data", [])
        d2 = page2.get("data", [])
        d1 = d1 if isinstance(d1, list) else [d1]
        d2 = d2 if isinstance(d2, list) else [d2]

        ids1 = [m.get("id") for m in d1]
        ids2 = [m.get("id") for m in d2]

        pagination_report = {
            "tested": True,
            "target_uuid": uuid,
            "total_messages_unpaginated": longest["message_count"],
            "page1_per_page1_len": len(d1),
            "page1_ids": ids1,
            "page2_per_page1_len": len(d2),
            "page2_ids": ids2,
            "per_page_param_respected": len(d1) == 1 and len(d2) <= 1,
            "page_param_changes_result": ids1 != ids2,
            "has_meta_block": "meta" in page1,
        }
        print(f"  page=1&per_page=1 → {len(d1)} item(s), ids={ids1}")
        print(f"  page=2&per_page=1 → {len(d2)} item(s), ids={ids2}")
        print(f"  'meta' key present in response: {'meta' in page1}")
        print(f"  per_page param respected: {pagination_report['per_page_param_respected']}")
        print(f"  page param changes result: {pagination_report['page_param_changes_result']}")

    # ── Summary ──────────────────────────────────────────────────────────────
    wall = round(inst.wall_time(), 1)
    print("\n── Summary ──────────────────────────────────────────────────────")
    print(f"  Total HTTP requests: {inst.request_count}")
    print(f"  Wall time: {wall}s")
    print(f"  429 responses encountered: {inst.throttle_429_count}")
    print(f"  5xx retries encountered: {inst.retry_5xx_count}")
    n_success = sum(1 for e in results if e["result"] == "success")
    n_empty = sum(1 for e in results if e["result"] == "empty")
    n_error = sum(1 for e in results if e["result"] == "error")
    print(f"  Results: {n_success} success / {n_empty} empty / {n_error} error (of {len(results)})")

    # ── Cache to local file for Q1 reuse ────────────────────────────────────
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    cache = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "full_history_window": {"start": FULL_HISTORY_START.isoformat(), "end": today.isoformat()},
        "total_reservations_in_window": len(all_res),
        "discovered_date_range": {"earliest": dated[0][1].isoformat(), "latest": dated[-1][1].isoformat()},
        "all_reservations_normalized": [
            {"uuid": r["uuid"], "check_in": d.isoformat(), "status": r.get("status")}
            for r, d in dated
        ],
        "sample_size": len(sample),
        "per_reservation_results": results,
        "pagination_report": pagination_report,
        "metrics": {
            "total_http_requests": inst.request_count,
            "wall_time_seconds": wall,
            "throttle_429_count": inst.throttle_429_count,
            "retry_5xx_count": inst.retry_5xx_count,
        },
    }
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, default=str)
    print(f"\n  Cached full results (incl. raw message bodies) to: {CACHE_PATH}")


if __name__ == "__main__":
    main()
