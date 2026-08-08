"""
Local SQLite store for the message-history corpus (Phase 3 Step 1 ingestion).

Two sources feed one unified `messages` table -- reservation threads and
inquiry threads -- so Step 2's retrieval layer can search across both without
a UNION. `source_type` + `source_uuid` map each row back to where it came
from; `entity_id` on a Finding (checks/finding.py) is the same UUID a drafter
would use to look up context here.

`ingest_state` is the resume checkpoint: one row per (source_type,
source_uuid) recording success/empty/error, mirroring the same three-way
distinction Session 0's probes used (never conflate empty with error). The
DB itself is the checkpoint -- there is no separate checkpoint file. Callers
process one entity per transaction (fetch, upsert messages, write state,
commit) so a crash mid-backfill can't corrupt or duplicate anything; on
restart, entities already `success`/`empty` are skipped.

`ingest_runs` records watermark history for the incremental update path
(hospitable/ingest.py) -- bookkeeping, not a correctness-critical checkpoint.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hospitable.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type           TEXT NOT NULL CHECK (source_type IN ('reservation', 'inquiry')),
    source_uuid           TEXT NOT NULL,
    platform_message_id   TEXT NOT NULL,
    sender                TEXT,
    body                  TEXT,
    content_type          TEXT,
    created_at            TEXT,
    property_uuid         TEXT,
    raw_json              TEXT NOT NULL,
    ingested_at           TEXT NOT NULL,
    UNIQUE (source_type, source_uuid, platform_message_id)
);

CREATE TABLE IF NOT EXISTS ingest_state (
    source_type      TEXT NOT NULL CHECK (source_type IN ('reservation', 'inquiry')),
    source_uuid      TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('success', 'empty', 'error')),
    message_count    INTEGER,
    error_detail     TEXT,
    last_fetched_at  TEXT NOT NULL,
    PRIMARY KEY (source_type, source_uuid)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    mode           TEXT NOT NULL CHECK (mode IN ('full', 'incremental')),
    started_at     TEXT NOT NULL,
    completed_at   TEXT,
    watermark      TEXT
);
"""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the local store and ensure schema exists."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ── Messages ─────────────────────────────────────────────────────────────────

def upsert_messages(
    conn: sqlite3.Connection,
    source_type: str,
    source_uuid: str,
    messages: list[dict[str, Any]],
    property_uuid: str | None = None,
) -> int:
    """
    Upsert one thread's messages. Safe to call repeatedly with the same data --
    re-processing an already-ingested thread updates existing rows in place
    (via the UNIQUE constraint's ON CONFLICT), never duplicates them.

    Does not commit -- caller controls the transaction boundary (one entity's
    upsert_messages + set_ingest_state should commit together).

    Returns the number of messages processed (inserted or refreshed).
    """
    now = _now()
    n = 0
    for m in messages:
        platform_message_id = str(m.get("id") or "")
        if not platform_message_id:
            continue
        conn.execute(
            """
            INSERT INTO messages
                (source_type, source_uuid, platform_message_id, sender, body,
                 content_type, created_at, property_uuid, raw_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_type, source_uuid, platform_message_id) DO UPDATE SET
                sender = excluded.sender,
                body = excluded.body,
                content_type = excluded.content_type,
                created_at = excluded.created_at,
                property_uuid = excluded.property_uuid,
                raw_json = excluded.raw_json,
                ingested_at = excluded.ingested_at
            """,
            (
                source_type, source_uuid, platform_message_id,
                m.get("sender"), m.get("body"), m.get("content_type"),
                m.get("created_at"), property_uuid,
                json.dumps(m, default=str), now,
            ),
        )
        n += 1
    return n


# ── Ingest state (resume checkpoint) ────────────────────────────────────────

def set_ingest_state(
    conn: sqlite3.Connection,
    source_type: str,
    source_uuid: str,
    status: str,
    message_count: int | None = None,
    error_detail: str | None = None,
) -> None:
    """Record success/empty/error for one entity. Does not commit."""
    conn.execute(
        """
        INSERT INTO ingest_state
            (source_type, source_uuid, status, message_count, error_detail, last_fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_type, source_uuid) DO UPDATE SET
            status = excluded.status,
            message_count = excluded.message_count,
            error_detail = excluded.error_detail,
            last_fetched_at = excluded.last_fetched_at
        """,
        (source_type, source_uuid, status, message_count, error_detail, _now()),
    )


def get_ingest_state(conn: sqlite3.Connection, source_type: str, source_uuid: str) -> dict | None:
    """Return the ingest_state row for one entity, or None if never processed."""
    row = conn.execute(
        "SELECT * FROM ingest_state WHERE source_type = ? AND source_uuid = ?",
        (source_type, source_uuid),
    ).fetchone()
    return dict(row) if row else None


def get_done_source_uuids(conn: sqlite3.Connection, source_type: str) -> set[str]:
    """
    source_uuids already successfully processed (status success or empty) --
    safe to skip on a resumed/re-run backfill. Entities marked `error` are
    NOT included, so they get retried.
    """
    rows = conn.execute(
        "SELECT source_uuid FROM ingest_state WHERE source_type = ? AND status IN ('success', 'empty')",
        (source_type,),
    ).fetchall()
    return {r["source_uuid"] for r in rows}


# ── Runs / watermark (incremental update path) ──────────────────────────────

def record_run(conn: sqlite3.Connection, mode: str, watermark: str | None) -> int:
    """
    Record a completed ingest run (full or incremental) with the watermark
    it observed. Commits. Returns the new run's id.
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO ingest_runs (mode, started_at, completed_at, watermark) VALUES (?, ?, ?, ?)",
        (mode, now, now, watermark),
    )
    conn.commit()
    return cur.lastrowid


def get_last_watermark(conn: sqlite3.Connection) -> str | None:
    """
    Most recent watermark from a completed run, or None if no run has
    completed yet (signals: do a full backfill, not an incremental one).
    """
    row = conn.execute(
        """
        SELECT watermark FROM ingest_runs
        WHERE completed_at IS NOT NULL AND watermark IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    return row["watermark"] if row else None
