"""
Tests for hospitable/store.py -- the local SQLite corpus store.

All tests use :memory: -- no filesystem, no API.
"""
from __future__ import annotations

import sqlite3

import pytest

from hospitable import store


def _conn() -> sqlite3.Connection:
    return store.connect(":memory:")


def _msg(msg_id: int, sender: str = "guest", body: str = "hello", created_at: str = "2026-01-01T00:00:00Z") -> dict:
    return {
        "id": msg_id,
        "sender": sender,
        "body": body,
        "content_type": "text/plain",
        "created_at": created_at,
    }


# ── Schema ───────────────────────────────────────────────────────────────────

def test_schema_creates_expected_tables():
    conn = _conn()
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"messages", "ingest_state", "ingest_runs"} <= tables


def test_messages_unique_constraint_exists():
    conn = _conn()
    indexes = conn.execute("PRAGMA index_list(messages)").fetchall()
    assert any(idx["unique"] for idx in indexes), "expected a UNIQUE index on messages"


def test_connect_is_idempotent_across_calls():
    """Reconnecting to the same (in this case, a fresh) store must not error on existing schema."""
    conn = _conn()
    # Re-running the schema script against the same connection should be a no-op.
    conn.executescript(store._SCHEMA)


# ── upsert_messages ──────────────────────────────────────────────────────────

def test_upsert_messages_inserts():
    conn = _conn()
    n = store.upsert_messages(conn, "inquiry", "inq-1", [_msg(1), _msg(2), _msg(3)])
    conn.commit()
    assert n == 3
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 3


def test_upsert_messages_is_idempotent():
    """Re-upserting the same thread must not create duplicate rows."""
    conn = _conn()
    msgs = [_msg(1), _msg(2)]
    store.upsert_messages(conn, "inquiry", "inq-1", msgs)
    store.upsert_messages(conn, "inquiry", "inq-1", msgs)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 2


def test_upsert_messages_updates_body_on_conflict():
    conn = _conn()
    store.upsert_messages(conn, "inquiry", "inq-1", [_msg(1, body="original")])
    store.upsert_messages(conn, "inquiry", "inq-1", [_msg(1, body="edited")])
    conn.commit()
    row = conn.execute("SELECT body FROM messages WHERE platform_message_id = '1'").fetchone()
    assert row["body"] == "edited"
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 1


def test_upsert_messages_same_platform_id_different_source_type_not_conflated():
    """A reservation and an inquiry could coincidentally share a platform message id -- must not collide."""
    conn = _conn()
    store.upsert_messages(conn, "inquiry", "inq-1", [_msg(1, body="from inquiry")])
    store.upsert_messages(conn, "reservation", "res-1", [_msg(1, body="from reservation")])
    conn.commit()
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 2


def test_upsert_messages_stores_property_uuid_and_raw_json():
    conn = _conn()
    store.upsert_messages(conn, "reservation", "res-1", [_msg(1)], property_uuid="prop-abc")
    conn.commit()
    row = conn.execute("SELECT property_uuid, raw_json FROM messages").fetchone()
    assert row["property_uuid"] == "prop-abc"
    assert '"id": 1' in row["raw_json"]


def test_upsert_messages_skips_entries_without_id():
    conn = _conn()
    n = store.upsert_messages(conn, "inquiry", "inq-1", [{"sender": "guest", "body": "no id field"}])
    conn.commit()
    assert n == 0
    count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert count == 0


# ── ingest_state ─────────────────────────────────────────────────────────────

def test_ingest_state_roundtrip_success():
    conn = _conn()
    store.set_ingest_state(conn, "inquiry", "inq-1", "success", message_count=5)
    conn.commit()
    state = store.get_ingest_state(conn, "inquiry", "inq-1")
    assert state["status"] == "success"
    assert state["message_count"] == 5
    assert state["error_detail"] is None


def test_ingest_state_error_records_detail():
    conn = _conn()
    store.set_ingest_state(conn, "reservation", "res-1", "error", error_detail="HTTP 500")
    conn.commit()
    state = store.get_ingest_state(conn, "reservation", "res-1")
    assert state["status"] == "error"
    assert state["error_detail"] == "HTTP 500"


def test_ingest_state_never_processed_returns_none():
    conn = _conn()
    assert store.get_ingest_state(conn, "inquiry", "nonexistent") is None


def test_ingest_state_transition_error_to_success_on_retry():
    """Resuming after a failure should overwrite the error state, not stack a second row."""
    conn = _conn()
    store.set_ingest_state(conn, "inquiry", "inq-1", "error", error_detail="timeout")
    conn.commit()
    store.set_ingest_state(conn, "inquiry", "inq-1", "success", message_count=3)
    conn.commit()
    state = store.get_ingest_state(conn, "inquiry", "inq-1")
    assert state["status"] == "success"
    assert state["message_count"] == 3
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM ingest_state WHERE source_type='inquiry' AND source_uuid='inq-1'"
    ).fetchone()["c"]
    assert count == 1


def test_get_done_source_uuids_includes_success_and_empty_excludes_error():
    conn = _conn()
    store.set_ingest_state(conn, "inquiry", "inq-success", "success", message_count=2)
    store.set_ingest_state(conn, "inquiry", "inq-empty", "empty", message_count=0)
    store.set_ingest_state(conn, "inquiry", "inq-error", "error", error_detail="boom")
    conn.commit()
    done = store.get_done_source_uuids(conn, "inquiry")
    assert done == {"inq-success", "inq-empty"}


def test_get_done_source_uuids_scoped_to_source_type():
    conn = _conn()
    store.set_ingest_state(conn, "inquiry", "shared-uuid", "success", message_count=1)
    conn.commit()
    assert store.get_done_source_uuids(conn, "inquiry") == {"shared-uuid"}
    assert store.get_done_source_uuids(conn, "reservation") == set()


# ── runs / watermark ─────────────────────────────────────────────────────────

def test_get_last_watermark_none_when_no_runs():
    conn = _conn()
    assert store.get_last_watermark(conn) is None


def test_record_run_and_get_last_watermark_roundtrip():
    conn = _conn()
    store.record_run(conn, "full", "2026-08-01T00:00:00Z")
    assert store.get_last_watermark(conn) == "2026-08-01T00:00:00Z"


def test_get_last_watermark_returns_most_recent_run():
    conn = _conn()
    store.record_run(conn, "full", "2026-08-01T00:00:00Z")
    store.record_run(conn, "incremental", "2026-08-05T00:00:00Z")
    assert store.get_last_watermark(conn) == "2026-08-05T00:00:00Z"


def test_record_run_without_watermark_not_returned_as_latest():
    """A run that completed without observing any messages (watermark=None) shouldn't
    clobber the last real watermark -- the incremental path needs a real floor to filter on."""
    conn = _conn()
    store.record_run(conn, "full", "2026-08-01T00:00:00Z")
    store.record_run(conn, "incremental", None)
    assert store.get_last_watermark(conn) == "2026-08-01T00:00:00Z"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
