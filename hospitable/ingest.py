"""
Ingestion orchestration for Phase 3 Step 1 -- backfills message threads into
the local store (hospitable/store.py).

Reuses hospitable/data.py's existing data-pull functions unmodified: no new
UUID resolution (none needed -- Session 0 confirmed reservation/inquiry UUIDs
work directly), no new throttle/retry logic (HospitableClient.get()'s
existing Retry-After/5xx backoff already covers rate limits, exercised live
during Session 0).
"""
from __future__ import annotations

import datetime
import logging
import sqlite3
from typing import Any

import requests

import hospitable.data as hdata
from hospitable.client import HospitableClient
from hospitable import store

log = logging.getLogger(__name__)

# Deliberately earlier than any plausible account start -- same approach
# Session 0 used to discover the account's true floor (2025-10-03) rather
# than assume one.
_FULL_HISTORY_START = datetime.date(2010, 1, 1)


def _extract_property_uuid(record: dict[str, Any]) -> str:
    """
    Same key-fallback approach as checks/_utils.py:extract_uuid, mirrored
    here rather than imported -- hospitable/ is the lower layer and checks/
    already depends on it, not the reverse.
    """
    for key in ("property_uuid", "property_id", "property", "properties"):
        val = record.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict):
                found = str(first.get("id") or first.get("uuid") or "")
                if found:
                    return found
        if isinstance(val, dict):
            found = str(val.get("id") or val.get("uuid") or "")
            if found:
                return found
    return ""


def backfill_inquiries(
    client: HospitableClient,
    conn: sqlite3.Connection,
    last_message_at: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """
    Backfill message threads for every inquiry in the account into the local
    store. Resumable: entities already `success`/`empty` in ingest_state are
    skipped without a network call; `error` entities are retried.

    Deliberately does NOT filter out closed/declined/expired inquiries the
    way checks/unanswered_inquiry.py does -- that check skips them because
    they're not actionable for a NEW reply, but this corpus still wants their
    message history as retrieval context. Every inquiry the account has is
    in scope here.

    property_uuid is resolved from the get_inquiries() LIST item, not the
    per-thread get_inquiry_thread() response -- confirmed live that the
    thread detail (include=messages,guest) carries no property field at all,
    while the list item (include=...,properties,listings) does. Mirrors
    checks/unanswered_inquiry.py's own resolution order.

    last_message_at: passed through to hdata.get_inquiries() -- when set,
    only inquiries with new activity since that timestamp come back. None
    (the default) means every inquiry in the account, i.e. a full backfill.
    See run_incremental().

    limit: process at most this many NOT-already-done entities (already-done
    ones are still skipped and don't count against it). For verifying a
    handful of real entities before trusting a full run -- does not affect
    the watermark (only run_incremental()/the CLI's non-limited path record
    one), so anything left unprocessed by a limited run is picked up by a
    later unlimited one.

    Returns a summary dict: total / skipped / success / empty / error /
    messages_ingested.
    """
    props = hdata.get_properties(client)
    prop_uuids = [p["uuid"] for p in props]

    inquiries = hdata.get_inquiries(client, prop_uuids, last_message_at=last_message_at)
    already_done = store.get_done_source_uuids(conn, "inquiry")

    summary = {
        "total": len(inquiries), "skipped": 0,
        "success": 0, "empty": 0, "error": 0,
        "messages_ingested": 0,
    }

    processed = 0
    for inquiry in inquiries:
        inq_uuid = inquiry.get("uuid") or ""
        if not inq_uuid:
            continue

        if inq_uuid in already_done:
            summary["skipped"] += 1
            continue

        if limit is not None and processed >= limit:
            log.info("Inquiry backfill: limit of %d reached, stopping early", limit)
            break
        processed += 1

        property_uuid = _extract_property_uuid(inquiry)

        try:
            thread = hdata.get_inquiry_thread(client, inq_uuid)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 410:
                # Converted to a reservation -- messages aren't reachable via
                # this endpoint anymore. Permanent, not transient: record as
                # `empty` (done, don't retry forever) with the reason noted.
                store.set_ingest_state(
                    conn, "inquiry", inq_uuid, "empty",
                    message_count=0, error_detail="410 converted to reservation",
                )
                conn.commit()
                summary["empty"] += 1
                log.info(
                    "[%d/%d] Inquiry %s -> empty (410, converted to reservation)",
                    processed, summary["total"], inq_uuid[:8],
                )
                continue
            store.set_ingest_state(
                conn, "inquiry", inq_uuid, "error",
                error_detail=f"HTTP {status_code}: {str(exc)[:150]}",
            )
            conn.commit()
            summary["error"] += 1
            log.warning(
                "[%d/%d] Inquiry %s -> error: HTTP %s",
                processed, summary["total"], inq_uuid[:8], status_code,
            )
            continue
        except Exception as exc:  # noqa: BLE001 -- record and continue, don't abort the backfill
            store.set_ingest_state(
                conn, "inquiry", inq_uuid, "error",
                error_detail=f"{type(exc).__name__}: {str(exc)[:150]}",
            )
            conn.commit()
            summary["error"] += 1
            log.warning(
                "[%d/%d] Inquiry %s -> error: %s",
                processed, summary["total"], inq_uuid[:8], type(exc).__name__,
            )
            continue

        messages = thread.get("messages") or []
        n = store.upsert_messages(conn, "inquiry", inq_uuid, messages, property_uuid=property_uuid or None)
        status = "success" if n > 0 else "empty"
        store.set_ingest_state(conn, "inquiry", inq_uuid, status, message_count=n)
        conn.commit()

        summary[status] += 1
        summary["messages_ingested"] += n
        log.info(
            "[%d/%d] Inquiry %s -> %s (%d messages)",
            processed, summary["total"], inq_uuid[:8], status, n,
        )

    return summary


def backfill_reservations(
    client: HospitableClient,
    conn: sqlite3.Connection,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
    last_message_at: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """
    Backfill message threads for every reservation in the account into the
    local store. Same resumable shape as backfill_inquiries(): entities
    already `success`/`empty` are skipped; `error` entities are retried.

    Includes every reservation status (accepted, cancelled, not accepted,
    etc.) -- same call Session 0's Q1/Q2 probes made: this is about
    corpus/thread reachability, not booking validity, and cancelled threads
    still carry valid retrieval-corpus content.

    Default date window is deliberately wide (2010-01-01 through today) to
    capture the account's true full history rather than assume a floor.

    property_uuid is resolved from the get_reservations() LIST item (same
    key-fallback checks/turnover_gap.py already relies on for reservations)
    -- get_reservation_messages() returns only the raw message list, no
    reservation attributes, so there's nothing to resolve it from there.

    No 410-style special case here (unlike backfill_inquiries) -- reservations
    don't convert to another entity type the way inquiries do; any fetch
    failure is recorded as a plain retryable `error`.

    last_message_at: passed through to hdata.get_reservations() -- when set,
    only reservations with new activity since that timestamp come back
    (still within the start/end window). None (the default) means every
    reservation in the window, i.e. a full backfill. See run_incremental().

    limit: process at most this many NOT-already-done entities (already-done
    ones are still skipped and don't count against it). Does not affect the
    watermark -- see backfill_inquiries()'s docstring for the same note.

    Returns a summary dict: total / skipped / success / empty / error /
    messages_ingested.
    """
    start = start or _FULL_HISTORY_START
    end = end or datetime.date.today()

    props = hdata.get_properties(client)
    prop_uuids = [p["uuid"] for p in props]

    reservations = hdata.get_reservations(client, prop_uuids, start, end, last_message_at=last_message_at)
    already_done = store.get_done_source_uuids(conn, "reservation")

    summary = {
        "total": len(reservations), "skipped": 0,
        "success": 0, "empty": 0, "error": 0,
        "messages_ingested": 0,
    }

    processed = 0
    for res in reservations:
        res_uuid = res.get("uuid") or ""
        if not res_uuid:
            continue

        if res_uuid in already_done:
            summary["skipped"] += 1
            continue

        if limit is not None and processed >= limit:
            log.info("Reservation backfill: limit of %d reached, stopping early", limit)
            break
        processed += 1

        property_uuid = _extract_property_uuid(res)

        try:
            messages = hdata.get_reservation_messages(client, res_uuid)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            store.set_ingest_state(
                conn, "reservation", res_uuid, "error",
                error_detail=f"HTTP {status_code}: {str(exc)[:150]}",
            )
            conn.commit()
            summary["error"] += 1
            log.warning(
                "[%d/%d] Reservation %s -> error: HTTP %s",
                processed, summary["total"], res_uuid[:8], status_code,
            )
            continue
        except Exception as exc:  # noqa: BLE001 -- record and continue, don't abort the backfill
            store.set_ingest_state(
                conn, "reservation", res_uuid, "error",
                error_detail=f"{type(exc).__name__}: {str(exc)[:150]}",
            )
            conn.commit()
            summary["error"] += 1
            log.warning(
                "[%d/%d] Reservation %s -> error: %s",
                processed, summary["total"], res_uuid[:8], type(exc).__name__,
            )
            continue

        n = store.upsert_messages(conn, "reservation", res_uuid, messages, property_uuid=property_uuid or None)
        status = "success" if n > 0 else "empty"
        store.set_ingest_state(conn, "reservation", res_uuid, status, message_count=n)
        conn.commit()

        summary[status] += 1
        summary["messages_ingested"] += n
        log.info(
            "[%d/%d] Reservation %s -> %s (%d messages)",
            processed, summary["total"], res_uuid[:8], status, n,
        )

    return summary


def run_incremental(client: HospitableClient, conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Run a backfill scoped to what's changed since the last completed run.

    Reads the watermark from store.get_last_watermark(). If there is no
    prior run, that's None, which backfill_inquiries()/backfill_reservations()
    already treat as "no filter" -- so the very first call here IS a full
    backfill, with no separate code path needed for that case.

    The new watermark is captured as "now" at the START of this run, not
    after -- deliberately conservative. Any message that arrives mid-run
    lands after this watermark, so the NEXT run's filter will still pick up
    that entity (re-processing it is a safe no-op/refresh via the UNIQUE
    constraint, not a duplicate) rather than silently missing it.

    Returns {"mode": "full"|"incremental", "watermark_used": str|None,
    "new_watermark": str, "inquiries": <backfill_inquiries summary>,
    "reservations": <backfill_reservations summary>}.
    """
    run_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
    watermark = store.get_last_watermark(conn)
    mode = "incremental" if watermark else "full"

    log.info("Ingest run starting: mode=%s watermark=%s", mode, watermark)

    inquiry_summary = backfill_inquiries(client, conn, last_message_at=watermark)
    reservation_summary = backfill_reservations(client, conn, last_message_at=watermark)

    store.record_run(conn, mode, run_start)

    log.info(
        "Ingest run complete: mode=%s inquiries=%s reservations=%s",
        mode, inquiry_summary, reservation_summary,
    )

    return {
        "mode": mode,
        "watermark_used": watermark,
        "new_watermark": run_start,
        "inquiries": inquiry_summary,
        "reservations": reservation_summary,
    }


# ── CLI entry point ──────────────────────────────────────────────────────────
#
# Run:
#   uv run -m hospitable.ingest              # full/incremental run (records
#                                             # a watermark -- the real thing)
#   uv run -m hospitable.ingest --limit N    # process at most N NOT-already-
#                                             # done entities PER SOURCE, does
#                                             # NOT record a watermark -- safe
#                                             # to re-run, nothing is skipped
#                                             # permanently by using this

def main() -> None:
    import sys

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    limit: int | None = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    client = HospitableClient()
    conn = store.connect()

    if limit is not None:
        log.info(
            "LIMITED run: at most %d new entity/entities per source. "
            "Watermark will NOT be recorded -- re-run without --limit later "
            "to pick up everything this run leaves unprocessed.",
            limit,
        )
        inquiry_summary = backfill_inquiries(client, conn, limit=limit)
        reservation_summary = backfill_reservations(client, conn, limit=limit)
        log.info(
            "Limited run complete. inquiries=%s reservations=%s",
            inquiry_summary, reservation_summary,
        )
    else:
        result = run_incremental(client, conn)
        log.info("Run complete: %s", result)


if __name__ == "__main__":
    main()
