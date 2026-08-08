"""
Tests for hospitable/ingest.py -- inquiry backfill orchestration (M2).

Mocks hospitable.data (hdata) functions directly rather than raw HTTP, since
this module's job is orchestration (skip/fetch/upsert/checkpoint), not the
HTTP layer -- that's already covered by tests/test_client.py.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import requests

os.environ.setdefault("HOSPITABLE_PAT", "test-pat")

import datetime

from hospitable import store
from hospitable.ingest import backfill_inquiries, backfill_reservations, run_incremental


def _conn():
    return store.connect(":memory:")


def _msg(msg_id: int, sender: str = "guest", body: str = "hi") -> dict:
    return {"id": msg_id, "sender": sender, "body": body, "content_type": "text/plain",
            "created_at": "2026-01-01T00:00:00Z"}


def _http_error(status_code: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status_code
    return requests.HTTPError(response=resp)


PROPS = [{"uuid": "prop-1", "name": "Test Property"}]

INQUIRIES = [
    {"uuid": "inq-1", "properties": {"id": "prop-1"}},
    {"uuid": "inq-2", "properties": {"id": "prop-1"}},
]


def _patched(get_properties=None, get_inquiries=None, get_inquiry_thread=None):
    return patch.multiple(
        "hospitable.ingest.hdata",
        get_properties=get_properties or (lambda client: PROPS),
        get_inquiries=get_inquiries or (lambda client, prop_uuids, last_message_at=None: INQUIRIES),
        get_inquiry_thread=get_inquiry_thread,
    )


# ── Full run ─────────────────────────────────────────────────────────────────

def test_backfill_inquiries_full_run_success_and_empty():
    conn = _conn()

    def thread(client, uuid):
        if uuid == "inq-1":
            return {"messages": [_msg(1), _msg(2)]}
        return {"messages": []}

    with _patched(get_inquiry_thread=thread):
        summary = backfill_inquiries(client=None, conn=conn)

    assert summary == {
        "total": 2, "skipped": 0, "success": 1, "empty": 1, "error": 0,
        "messages_ingested": 2,
    }
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 2
    state1 = store.get_ingest_state(conn, "inquiry", "inq-1")
    state2 = store.get_ingest_state(conn, "inquiry", "inq-2")
    assert state1["status"] == "success" and state1["message_count"] == 2
    assert state2["status"] == "empty" and state2["message_count"] == 0


def test_backfill_inquiries_property_uuid_resolved_from_list_not_thread():
    """Confirmed live: get_inquiry_thread()'s response carries no property field at all --
    property_uuid must come from the get_inquiries() list item."""
    conn = _conn()

    def thread(client, uuid):
        # Mirrors the real API: no property/properties key in the thread detail.
        return {"messages": [_msg(1)], "arrival_date": "2026-01-01"}

    with _patched(get_inquiry_thread=thread):
        backfill_inquiries(client=None, conn=conn)

    row = conn.execute("SELECT property_uuid FROM messages WHERE source_uuid = 'inq-1'").fetchone()
    assert row["property_uuid"] == "prop-1"


# ── Resume / idempotency ─────────────────────────────────────────────────────

def test_backfill_inquiries_resume_skips_already_done():
    conn = _conn()
    store.set_ingest_state(conn, "inquiry", "inq-1", "success", message_count=2)
    conn.commit()

    calls = []

    def thread(client, uuid):
        calls.append(uuid)
        return {"messages": [_msg(1)]}

    with _patched(get_inquiry_thread=thread):
        summary = backfill_inquiries(client=None, conn=conn)

    assert calls == ["inq-2"]  # inq-1 skipped, never fetched
    assert summary["skipped"] == 1
    assert summary["success"] == 1


def test_backfill_inquiries_rerun_is_idempotent_and_does_not_refetch():
    conn = _conn()
    calls = []

    def thread(client, uuid):
        calls.append(uuid)
        return {"messages": [_msg(1), _msg(2)]}

    with _patched(get_inquiry_thread=thread):
        backfill_inquiries(client=None, conn=conn)
        first_call_count = len(calls)
        summary2 = backfill_inquiries(client=None, conn=conn)

    assert first_call_count == 2  # both inquiries fetched on first run
    assert len(calls) == 2  # no additional fetches on the second run
    assert summary2["skipped"] == 2
    assert summary2["success"] == 0
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 4  # 2 messages x 2 inquiries, not duplicated


# ── 410 handling ─────────────────────────────────────────────────────────────

def test_backfill_inquiries_410_recorded_as_empty_not_error():
    conn = _conn()

    def thread(client, uuid):
        if uuid == "inq-1":
            raise _http_error(410)
        return {"messages": [_msg(1)]}

    with _patched(get_inquiry_thread=thread):
        summary = backfill_inquiries(client=None, conn=conn)

    assert summary["error"] == 0
    assert summary["empty"] == 1
    state = store.get_ingest_state(conn, "inquiry", "inq-1")
    assert state["status"] == "empty"
    assert "410" in state["error_detail"]


# ── Error handling + retry on resume ─────────────────────────────────────────

def test_backfill_inquiries_error_recorded_and_not_conflated_with_empty():
    conn = _conn()

    def thread(client, uuid):
        if uuid == "inq-1":
            raise _http_error(500)
        return {"messages": []}

    with _patched(get_inquiry_thread=thread):
        summary = backfill_inquiries(client=None, conn=conn)

    assert summary["error"] == 1
    assert summary["empty"] == 1  # inq-2's genuine empty thread, distinct from inq-1's error
    state = store.get_ingest_state(conn, "inquiry", "inq-1")
    assert state["status"] == "error"
    assert "500" in state["error_detail"]


def test_backfill_inquiries_error_entity_retried_on_next_run():
    conn = _conn()
    attempt = {"count": 0}

    def flaky_thread(client, uuid):
        if uuid == "inq-1" and attempt["count"] == 0:
            attempt["count"] += 1
            raise _http_error(500)
        return {"messages": [_msg(1)]}

    with _patched(get_inquiry_thread=flaky_thread):
        summary1 = backfill_inquiries(client=None, conn=conn)
        assert summary1["error"] == 1
        state = store.get_ingest_state(conn, "inquiry", "inq-1")
        assert state["status"] == "error"

        summary2 = backfill_inquiries(client=None, conn=conn)

    # inq-1 retried (was `error`, not skippable) and now succeeds; inq-2 skipped (already done)
    assert summary2["skipped"] == 1
    assert summary2["success"] == 1
    state = store.get_ingest_state(conn, "inquiry", "inq-1")
    assert state["status"] == "success"


# ── Misc ─────────────────────────────────────────────────────────────────────

def test_backfill_inquiries_skips_entries_without_uuid():
    conn = _conn()

    def get_inquiries(client, prop_uuids, last_message_at=None):
        return [{"properties": {"id": "prop-1"}}]  # no 'uuid' key

    with _patched(get_inquiries=get_inquiries, get_inquiry_thread=lambda c, u: {"messages": []}):
        summary = backfill_inquiries(client=None, conn=conn)

    assert summary["success"] == 0 and summary["empty"] == 0 and summary["error"] == 0
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 0


def test_backfill_inquiries_does_not_filter_closed_statuses():
    """Deliberately different from checks/unanswered_inquiry.py: this ingests
    every inquiry's history regardless of status, since closed/declined
    inquiries still carry valid retrieval-corpus content."""
    conn = _conn()

    def get_inquiries(client, prop_uuids, last_message_at=None):
        return [{"uuid": "inq-declined", "status": "declined", "properties": {"id": "prop-1"}}]

    with _patched(get_inquiries=get_inquiries, get_inquiry_thread=lambda c, u: {"messages": [_msg(1)]}):
        summary = backfill_inquiries(client=None, conn=conn)

    assert summary["success"] == 1


# ── limit (M5 pre-flight) ─────────────────────────────────────────────────────

def test_backfill_inquiries_limit_caps_processed_entities():
    conn = _conn()
    three = [
        {"uuid": "inq-1", "properties": {"id": "prop-1"}},
        {"uuid": "inq-2", "properties": {"id": "prop-1"}},
        {"uuid": "inq-3", "properties": {"id": "prop-1"}},
    ]
    calls = []

    def get_inquiries(client, prop_uuids, last_message_at=None):
        return three

    def thread(client, uuid):
        calls.append(uuid)
        return {"messages": [_msg(1)]}

    with _patched(get_inquiries=get_inquiries, get_inquiry_thread=thread):
        summary = backfill_inquiries(client=None, conn=conn, limit=2)

    assert calls == ["inq-1", "inq-2"]  # inq-3 never fetched
    assert summary["success"] == 2
    assert store.get_ingest_state(conn, "inquiry", "inq-3") is None


def test_backfill_inquiries_limit_does_not_count_already_done():
    conn = _conn()
    store.set_ingest_state(conn, "inquiry", "inq-1", "success", message_count=1)
    conn.commit()
    calls = []

    def thread(client, uuid):
        calls.append(uuid)
        return {"messages": [_msg(1)]}

    with _patched(get_inquiry_thread=thread):
        summary = backfill_inquiries(client=None, conn=conn, limit=1)

    assert calls == ["inq-2"]  # inq-1 was already done -- skip doesn't consume the limit
    assert summary["skipped"] == 1
    assert summary["success"] == 1


def test_backfill_inquiries_limit_none_processes_everything():
    """Sanity check: omitting limit (the default, used by run_incremental())
    is unaffected by this change."""
    conn = _conn()
    with _patched(get_inquiry_thread=lambda c, u: {"messages": [_msg(1)]}):
        summary = backfill_inquiries(client=None, conn=conn)

    assert summary["success"] == 2  # both INQUIRIES processed, no cap


def test_backfill_reservations_limit_caps_processed_entities():
    conn = _conn()
    three = [
        {"uuid": "res-1", "properties": {"id": "prop-1"}},
        {"uuid": "res-2", "properties": {"id": "prop-1"}},
        {"uuid": "res-3", "properties": {"id": "prop-1"}},
    ]
    calls = []

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        return three

    def messages(client, uuid):
        calls.append(uuid)
        return [_msg(1)]

    with _patched_res(get_reservations=get_reservations, get_reservation_messages=messages):
        summary = backfill_reservations(client=None, conn=conn, limit=2)

    assert calls == ["res-1", "res-2"]  # res-3 never fetched
    assert summary["success"] == 2
    assert store.get_ingest_state(conn, "reservation", "res-3") is None


def test_backfill_reservations_limit_does_not_count_already_done():
    conn = _conn()
    store.set_ingest_state(conn, "reservation", "res-1", "success", message_count=1)
    conn.commit()
    calls = []

    def messages(client, uuid):
        calls.append(uuid)
        return [_msg(1)]

    with _patched_res(get_reservation_messages=messages):
        summary = backfill_reservations(client=None, conn=conn, limit=1)

    assert calls == ["res-2"]
    assert summary["skipped"] == 1
    assert summary["success"] == 1


def test_limit_run_does_not_advance_watermark():
    """A --limit run must not record a run/watermark -- it deliberately
    leaves entities unprocessed, so a later unlimited run needs to see
    everything as still outstanding."""
    conn = _conn()
    with _patched(get_inquiry_thread=lambda c, u: {"messages": [_msg(1)]}):
        backfill_inquiries(client=None, conn=conn, limit=1)

    assert store.get_last_watermark(conn) is None
    runs = conn.execute("SELECT COUNT(*) AS c FROM ingest_runs").fetchone()["c"]
    assert runs == 0


# ── backfill_reservations (M3) ───────────────────────────────────────────────

RESERVATIONS = [
    {"uuid": "res-1", "status": "accepted", "properties": {"id": "prop-1"}},
    {"uuid": "res-2", "status": "cancelled", "properties": {"id": "prop-1"}},
]


def _patched_res(get_properties=None, get_reservations=None, get_reservation_messages=None):
    return patch.multiple(
        "hospitable.ingest.hdata",
        get_properties=get_properties or (lambda client: PROPS),
        get_reservations=get_reservations or (lambda client, prop_uuids, start, end, last_message_at=None: RESERVATIONS),
        get_reservation_messages=get_reservation_messages,
    )


def test_backfill_reservations_full_run_success_and_empty():
    conn = _conn()

    def messages(client, uuid):
        if uuid == "res-1":
            return [_msg(1), _msg(2), _msg(3)]
        return []

    with _patched_res(get_reservation_messages=messages):
        summary = backfill_reservations(client=None, conn=conn)

    assert summary == {
        "total": 2, "skipped": 0, "success": 1, "empty": 1, "error": 0,
        "messages_ingested": 3,
    }
    count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE source_type='reservation'").fetchone()["c"]
    assert count == 3
    state1 = store.get_ingest_state(conn, "reservation", "res-1")
    state2 = store.get_ingest_state(conn, "reservation", "res-2")
    assert state1["status"] == "success" and state1["message_count"] == 3
    assert state2["status"] == "empty" and state2["message_count"] == 0


def test_backfill_reservations_property_uuid_resolved_from_list():
    conn = _conn()
    with _patched_res(get_reservation_messages=lambda c, u: [_msg(1)]):
        backfill_reservations(client=None, conn=conn)

    row = conn.execute(
        "SELECT property_uuid FROM messages WHERE source_uuid = 'res-1'"
    ).fetchone()
    assert row["property_uuid"] == "prop-1"


def test_backfill_reservations_resume_skips_already_done():
    conn = _conn()
    store.set_ingest_state(conn, "reservation", "res-1", "success", message_count=3)
    conn.commit()

    calls = []

    def messages(client, uuid):
        calls.append(uuid)
        return [_msg(1)]

    with _patched_res(get_reservation_messages=messages):
        summary = backfill_reservations(client=None, conn=conn)

    assert calls == ["res-2"]
    assert summary["skipped"] == 1
    assert summary["success"] == 1


def test_backfill_reservations_rerun_is_idempotent_and_does_not_refetch():
    conn = _conn()
    calls = []

    def messages(client, uuid):
        calls.append(uuid)
        return [_msg(1), _msg(2)]

    with _patched_res(get_reservation_messages=messages):
        backfill_reservations(client=None, conn=conn)
        first_call_count = len(calls)
        summary2 = backfill_reservations(client=None, conn=conn)

    assert first_call_count == 2
    assert len(calls) == 2  # no additional fetches on the second run
    assert summary2["skipped"] == 2
    count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE source_type='reservation'").fetchone()["c"]
    assert count == 4  # 2 messages x 2 reservations, not duplicated


def test_backfill_reservations_error_recorded_and_retried_on_next_run():
    conn = _conn()
    attempt = {"count": 0}

    def flaky_messages(client, uuid):
        if uuid == "res-1" and attempt["count"] == 0:
            attempt["count"] += 1
            raise _http_error(500)
        return [_msg(1)]

    with _patched_res(get_reservation_messages=flaky_messages):
        summary1 = backfill_reservations(client=None, conn=conn)
        assert summary1["error"] == 1
        state = store.get_ingest_state(conn, "reservation", "res-1")
        assert state["status"] == "error"
        assert "500" in state["error_detail"]

        summary2 = backfill_reservations(client=None, conn=conn)

    assert summary2["skipped"] == 1  # res-2 already done
    assert summary2["success"] == 1  # res-1 retried, now succeeds
    state = store.get_ingest_state(conn, "reservation", "res-1")
    assert state["status"] == "success"


def test_backfill_reservations_includes_all_statuses():
    """Deliberately mirrors the inquiry decision: cancelled/not-accepted
    reservations still carry valid retrieval-corpus content."""
    conn = _conn()

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        return [{"uuid": "res-cancelled", "status": "cancelled", "properties": {"id": "prop-1"}}]

    with _patched_res(get_reservations=get_reservations, get_reservation_messages=lambda c, u: [_msg(1)]):
        summary = backfill_reservations(client=None, conn=conn)

    assert summary["success"] == 1


def test_backfill_reservations_skips_entries_without_uuid():
    conn = _conn()

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        return [{"status": "accepted", "properties": {"id": "prop-1"}}]  # no 'uuid'

    with _patched_res(get_reservations=get_reservations, get_reservation_messages=lambda c, u: []):
        summary = backfill_reservations(client=None, conn=conn)

    assert summary["success"] == 0 and summary["empty"] == 0 and summary["error"] == 0
    count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE source_type='reservation'").fetchone()["c"]
    assert count == 0


def test_backfill_reservations_default_window_is_wide_and_ends_today():
    conn = _conn()
    captured = {}

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        captured["start"] = start
        captured["end"] = end
        return []

    with _patched_res(get_reservations=get_reservations):
        backfill_reservations(client=None, conn=conn)

    assert captured["start"] == datetime.date(2010, 1, 1)
    assert captured["end"] == datetime.date.today()


def test_backfill_reservations_explicit_window_overrides_default():
    conn = _conn()
    captured = {}

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        captured["start"] = start
        captured["end"] = end
        return []

    custom_start = datetime.date(2026, 1, 1)
    custom_end = datetime.date(2026, 6, 1)
    with _patched_res(get_reservations=get_reservations):
        backfill_reservations(client=None, conn=conn, start=custom_start, end=custom_end)

    assert captured["start"] == custom_start
    assert captured["end"] == custom_end


# ── run_incremental (M4) ─────────────────────────────────────────────────────

def _patched_both(
    get_properties=None,
    get_inquiries=None,
    get_inquiry_thread=None,
    get_reservations=None,
    get_reservation_messages=None,
):
    return patch.multiple(
        "hospitable.ingest.hdata",
        get_properties=get_properties or (lambda client: PROPS),
        get_inquiries=get_inquiries or (lambda client, prop_uuids, last_message_at=None: []),
        get_inquiry_thread=get_inquiry_thread or (lambda client, uuid: {"messages": []}),
        get_reservations=get_reservations or (lambda client, prop_uuids, start, end, last_message_at=None: []),
        get_reservation_messages=get_reservation_messages or (lambda client, uuid: []),
    )


def test_run_incremental_first_run_has_no_watermark_and_is_full_backfill():
    conn = _conn()
    captured = {}

    def get_inquiries(client, prop_uuids, last_message_at=None):
        captured["inquiries_last_message_at"] = last_message_at
        return []

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        captured["reservations_last_message_at"] = last_message_at
        return []

    with _patched_both(get_inquiries=get_inquiries, get_reservations=get_reservations):
        result = run_incremental(client=None, conn=conn)

    assert result["mode"] == "full"
    assert result["watermark_used"] is None
    assert captured["inquiries_last_message_at"] is None
    assert captured["reservations_last_message_at"] is None
    assert store.get_last_watermark(conn) == result["new_watermark"]


def test_run_incremental_uses_prior_watermark_when_present():
    conn = _conn()
    store.record_run(conn, "full", "2026-08-01T00:00:00Z")
    captured = {}

    def get_inquiries(client, prop_uuids, last_message_at=None):
        captured["inquiries_last_message_at"] = last_message_at
        return []

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        captured["reservations_last_message_at"] = last_message_at
        return []

    with _patched_both(get_inquiries=get_inquiries, get_reservations=get_reservations):
        result = run_incremental(client=None, conn=conn)

    assert result["mode"] == "incremental"
    assert result["watermark_used"] == "2026-08-01T00:00:00Z"
    assert captured["inquiries_last_message_at"] == "2026-08-01T00:00:00Z"
    assert captured["reservations_last_message_at"] == "2026-08-01T00:00:00Z"


def test_run_incremental_records_new_watermark_after_run():
    conn = _conn()
    store.record_run(conn, "full", "2026-08-01T00:00:00Z")

    with _patched_both():
        result = run_incremental(client=None, conn=conn)

    new_watermark = store.get_last_watermark(conn)
    assert new_watermark == result["new_watermark"]
    assert new_watermark > "2026-08-01T00:00:00Z"  # ISO8601 strings sort chronologically


def test_run_incremental_processes_both_sources():
    conn = _conn()

    def get_inquiries(client, prop_uuids, last_message_at=None):
        return [{"uuid": "inq-1", "properties": {"id": "prop-1"}}]

    def get_inquiry_thread(client, uuid):
        return {"messages": [_msg(1)]}

    def get_reservations(client, prop_uuids, start, end, last_message_at=None):
        return [{"uuid": "res-1", "properties": {"id": "prop-1"}}]

    def get_reservation_messages(client, uuid):
        return [_msg(2)]

    with _patched_both(
        get_inquiries=get_inquiries,
        get_inquiry_thread=get_inquiry_thread,
        get_reservations=get_reservations,
        get_reservation_messages=get_reservation_messages,
    ):
        result = run_incremental(client=None, conn=conn)

    assert result["inquiries"]["success"] == 1
    assert result["reservations"]["success"] == 1
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 2


def test_run_incremental_second_run_uses_incremental_mode_and_no_duplicate_runs_issue():
    conn = _conn()
    with _patched_both():
        run_incremental(client=None, conn=conn)
        result2 = run_incremental(client=None, conn=conn)

    assert result2["mode"] == "incremental"  # a watermark now exists from the first run
    runs = conn.execute("SELECT COUNT(*) AS c FROM ingest_runs").fetchone()["c"]
    assert runs == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
