"""
THROWAWAY PROBE — Session 0 Q1: corpus count for the retrieval decision.

Not production ingestion code: no resumability, no idempotency, no
production error handling. Disposable script, kept only for its output
data and findings (see logs/session0_q1_cache.json, gitignored).

Reuses Q2's already-fetched data instead of re-pulling it: loads
logs/session0_q2_cache.json for the 160-reservation list (already pulled
via hdata.get_reservations()) and the 20 oldest reservations' message
threads (already fetched via hdata.get_reservation_messages()). Fetches
the remaining 140 threads via the same reused path, merges everything into
a NEW cache file (session0_q2_cache.json is left untouched — session0_q2.md
already cites specific numbers from that exact file).

No sampling, no extrapolation — this pulls message threads for the full
160-reservation history, since the population is small enough that the
exact count costs about the same to get as a partial sample would.

Read-only, GET only. Does not modify checks/*.py or hospitable/data.py.

Run:
    uv run python prototypes/message_corpus_probe.py

PII policy: console output prints sender/role, message counts, and short
non-content metadata only — no guest names, emails, phones, or message body
text. The cache file DOES contain raw message bodies (needed for the token
estimate) but lives under logs/, which is gitignored — never committed.
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q2_CACHE_PATH = os.path.join(REPO_ROOT, "logs", "session0_q2_cache.json")
Q1_CACHE_PATH = os.path.join(REPO_ROOT, "logs", "session0_q1_cache.json")


# ── Request/throttle instrumentation (same pattern as Q2's probe) ──────────

class _Instrumentation:
    def __init__(self) -> None:
        self.request_count = 0
        self.throttle_429_count = 0
        self.retry_5xx_count = 0
        self.start_monotonic = time.monotonic()

    def wrap_session(self, session) -> None:
        orig_get = session.get

        def counted_get(url, params=None, **kwargs):
            self.request_count += 1
            return orig_get(url, params=params, **kwargs)

        session.get = counted_get

    def wall_time(self) -> float:
        return time.monotonic() - self.start_monotonic


class _ThrottleLogHandler(logging.Handler):
    def __init__(self, inst: _Instrumentation) -> None:
        super().__init__(level=logging.WARNING)
        self.inst = inst

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "429" in msg:
            self.inst.throttle_429_count += 1
        elif "retrying in" in msg:
            self.inst.retry_5xx_count += 1


def main() -> None:
    inst = _Instrumentation()
    logging.getLogger("hospitable.client").addHandler(_ThrottleLogHandler(inst))
    logging.getLogger("hospitable.client").setLevel(logging.WARNING)

    print("── Loading Q2 cache ────────────────────────────────────────────")
    with open(Q2_CACHE_PATH) as f:
        q2 = json.load(f)

    all_reservations = q2["all_reservations_normalized"]  # [{uuid, check_in, status}, ...]
    already_fetched = {e["reservation_uuid"]: e for e in q2["per_reservation_results"]}
    print(f"  {len(all_reservations)} total reservations, "
          f"{len(already_fetched)} threads already fetched by Q2 (reusing, not re-fetching)")

    client = HospitableClient()
    inst.wrap_session(client._session)

    print("\n── Fetching remaining threads ─────────────────────────────────")
    results: list[dict] = []
    to_fetch = [r for r in all_reservations if r["uuid"] not in already_fetched]
    print(f"  {len(to_fetch)} reservations need fetching")

    for i, res in enumerate(all_reservations):
        res_uuid = res["uuid"]

        if res_uuid in already_fetched:
            entry = dict(already_fetched[res_uuid])
            entry["source"] = "reused_from_q2"
            results.append(entry)
            continue

        t0 = time.monotonic()
        entry = {
            "reservation_uuid": res_uuid,
            "check_in": res["check_in"],
            "status": res.get("status"),
            "source": "fetched_new",
        }
        try:
            msgs = hdata.get_reservation_messages(client, res_uuid)
        except Exception as exc:  # noqa: BLE001 — throwaway probe
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            entry["result"] = "error"
            entry["error_type"] = type(exc).__name__
            entry["error_status_code"] = status_code
            entry["error_detail"] = str(exc)[:200]
            entry["fetch_seconds"] = round(time.monotonic() - t0, 2)
            print(f"  [{i}] {res_uuid[:8]} ERROR {type(exc).__name__} (HTTP {status_code})")
            results.append(entry)
            continue

        fetch_s = round(time.monotonic() - t0, 2)
        entry["fetch_seconds"] = fetch_s
        entry["message_count"] = len(msgs)
        entry["messages_raw"] = msgs
        entry["senders_seen"] = sorted({m.get("sender") for m in msgs if m.get("sender")})

        if msgs:
            entry["result"] = "success"
        else:
            entry["result"] = "empty"

        if (i + 1) % 10 == 0 or i == len(all_reservations) - 1:
            print(f"  [{i}] {res_uuid[:8]} {entry['result']} "
                  f"({entry.get('message_count', 0)} msgs, {fetch_s}s) "
                  f"— {inst.request_count} requests so far, {round(inst.wall_time())}s elapsed")

        results.append(entry)

    wall = round(inst.wall_time(), 1)

    # ── Corpus stats ─────────────────────────────────────────────────────────
    print("\n── Corpus stats ─────────────────────────────────────────────────")
    n_success = sum(1 for e in results if e["result"] == "success")
    n_empty = sum(1 for e in results if e["result"] == "empty")
    n_error = sum(1 for e in results if e["result"] == "error")
    n_reused = sum(1 for e in results if e.get("source") == "reused_from_q2")
    print(f"  {len(results)} reservations total "
          f"({n_success} success / {n_empty} empty / {n_error} error, {n_reused} reused from Q2)")

    per_thread_counts = [e["message_count"] for e in results if "message_count" in e]
    total_messages = sum(per_thread_counts)
    sorted_counts = sorted(per_thread_counts)
    n = len(sorted_counts)
    median = (
        sorted_counts[n // 2] if n % 2 == 1
        else (sorted_counts[n // 2 - 1] + sorted_counts[n // 2]) / 2
    ) if n else 0

    print(f"  Total messages: {total_messages}")
    print(f"  Messages per conversation: min={min(per_thread_counts) if per_thread_counts else 0} "
          f"median={median} max={max(per_thread_counts) if per_thread_counts else 0}")

    # Word-count-based token estimate: ~0.75 tokens/word is the commonly used
    # rule of thumb for English text (tiktoken is not a project dependency —
    # not adding it for a throwaway probe).
    total_words = 0
    for e in results:
        for m in e.get("messages_raw", []):
            body = m.get("body") or ""
            total_words += len(str(body).split())
    est_tokens = round(total_words / 0.75)
    print(f"  Total words across all message bodies: {total_words}")
    print(f"  Estimated tokens (words / 0.75): ~{est_tokens}")

    checkins = [datetime.date.fromisoformat(e["check_in"]) for e in results]
    print(f"  Date range covered: {min(checkins)} → {max(checkins)}")

    print(f"\n  Total HTTP requests this run: {inst.request_count}")
    print(f"  Wall time this run: {wall}s")
    print(f"  429s this run: {inst.throttle_429_count}")
    print(f"  5xx retries this run: {inst.retry_5xx_count}")

    # ── Cache ────────────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(Q1_CACHE_PATH), exist_ok=True)
    cache = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reused_from_q2_cache": Q2_CACHE_PATH,
        "total_reservations": len(results),
        "per_reservation_results": results,
        "stats": {
            "total_messages": total_messages,
            "messages_per_conversation_min": min(per_thread_counts) if per_thread_counts else 0,
            "messages_per_conversation_median": median,
            "messages_per_conversation_max": max(per_thread_counts) if per_thread_counts else 0,
            "total_words": total_words,
            "estimated_tokens": est_tokens,
            "token_estimate_method": "word_count / 0.75",
            "date_range_earliest": min(checkins).isoformat(),
            "date_range_latest": max(checkins).isoformat(),
            "n_success": n_success,
            "n_empty": n_empty,
            "n_error": n_error,
            "n_reused_from_q2": n_reused,
        },
        "this_run_metrics": {
            "total_http_requests": inst.request_count,
            "wall_time_seconds": wall,
            "throttle_429_count": inst.throttle_429_count,
            "retry_5xx_count": inst.retry_5xx_count,
        },
    }
    with open(Q1_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, default=str)
    print(f"\n  Cached full results to: {Q1_CACHE_PATH}")


if __name__ == "__main__":
    main()
