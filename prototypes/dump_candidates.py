#!/usr/bin/env python3
"""
Step 2 prep -- dump candidate material from the real corpus for hand-labeling.

Does NOT invent queries, does NOT pick the 15-20 eval queries, does NOT write
expected answers. It surfaces real guest-authored message content, grouped
and keyword-clustered, so a human can pick real queries with known-good
answers by eye. This must run BEFORE any BM25/embedding code exists -- eval
sets built after seeing retrieval output are grading on a curve.

Column names are hardcoded against the verified hospitable/store.py schema
(id, source_type, source_uuid, platform_message_id, sender, body,
content_type, created_at, property_uuid, raw_json, ingested_at) -- not
guessed. Verified live against data/hospitable.db (1,455 rows: 667 guest /
788 host, 113 inquiry / 1,342 reservation messages) before writing this.

Filtering notes (checked empirically, not assumed):
  - 15 rows have NULL body -- all confirmed to be image-attachment-only
    messages (no text), never conversational content. Excluded, same as
    the original script already did.
  - raw_json's "source" field (platform/hospitable/automated/AI) describes
    who/what COMPOSED a host reply, not whether it's system noise -- all
    four are legitimate message content. Not filtered. Shown as metadata
    instead, so a human labeler can see at a glance whether an answer
    candidate is a canned/templated send (hospitable/automated) vs. an
    ad-hoc human or AI-assisted reply (platform/AI) when judging whether
    it's a good "known-good answer" for a query.

Usage:
    python prototypes/dump_candidates.py data/hospitable.db > logs/candidates.txt
    python prototypes/dump_candidates.py data/hospitable.db --schema-only
"""
import html
import random
import sqlite3
import sys
import textwrap
from collections import defaultdict


def _clean(body: str) -> str:
    """HTML-unescape (some bodies contain entities like &#39;) and flatten newlines for display."""
    return html.unescape(body).replace("\n", " ")

TOPIC_KEYWORDS = {
    "parking": ["park", "parking", "garage", "car"],
    "check-in/out timing": ["early check", "late check", "check-in time", "checkout time", "check out time"],
    "noise": ["noise", "loud", "party", "neighbor"],
    "wifi": ["wifi", "wi-fi", "internet", "password"],
    "door/lock/access": ["door code", "lock", "key", "access code", "entry"],
    "amenities": ["towel", "coffee", "washer", "dryer", "iron", "hair dryer"],
    "pets": ["dog", "cat", "pet"],
    "directions/location": ["address", "directions", "how do i get", "uber", "taxi"],
    "cancellation/refund": ["cancel", "refund", "reschedule"],
    "complaints/issues": ["broken", "not working", "dirty", "smell", "issue", "problem", "complain"],
    # Original keywords ("extra guest", "additional guest", "more people")
    # returned 0 matches against the real corpus -- checked for alternate
    # phrasing rather than leave a silently-empty topic. Found one genuine
    # hit ("inviting my friends over for dinner") using different wording;
    # added that phrasing. The rest of this account's "friend" mentions are
    # about a travel companion who's part of the booking, not an
    # unregistered/extra occupant -- a real distinction, not a keyword miss.
    "extra guests": ["extra guest", "additional guest", "more people", "friends over", "guests over"],
    # Added from real corpus content, not guessed: this is a shared-room
    # property and "is this space shared / is another guest in the other
    # room" is one of the most common recurring guest questions -- 10/10
    # sampled hits were genuinely about this, confirmed by reading them.
    "shared space / other guest": [
        "shared", "share the", "other guest", "roommate",
        "to myself", "whole thing", "shared bathroom", "shared space",
    ],
    # Added from real corpus content: confirmed Spanish-language guest
    # messages exist, including at least one substantive one (a gas-smell
    # report). Relevant given this project's own history flagged a
    # Spanish-inquiry-answered-in-English incident, and language mismatch
    # is an explicit deferred check in PHASE3_PLAN.md.
    "non-English (Spanish)": [
        "gracias", "hola", "por favor", "buenos días", "buenas noches",
        "muchas gracias", "cuándo", "dónde",
    ],
}

# Verified against the real schema -- see module docstring.
ID_COL = "id"
SOURCE_TYPE_COL = "source_type"
ENTITY_COL = "source_uuid"
SENDER_COL = "sender"
TS_COL = "created_at"
BODY_COL = "body"


def dump_schema(conn):
    print("=" * 70)
    print("SCHEMA")
    print("=" * 70)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        print(f"\n-- {t} --")
        for row in conn.execute(f"PRAGMA table_info({t})"):
            print(f"  {row[1]:20s} {row[2]}")
    print()


def _fetch_guest_rows(conn):
    """
    Guest-authored, non-empty messages only -- these are the candidate
    QUERIES (what a guest actually asked), which is what Parts 1 and 3 are
    for. Part 2 (thread view) fetches both senders separately, since a
    thread's value is in showing the host's answer too.
    """
    query = f"""
        SELECT {ID_COL}, {SOURCE_TYPE_COL}, {ENTITY_COL}, {SENDER_COL}, {TS_COL}, {BODY_COL}
        FROM messages
        WHERE {SENDER_COL} = 'guest'
          AND {BODY_COL} IS NOT NULL AND TRIM({BODY_COL}) != ''
        ORDER BY {ENTITY_COL}, {TS_COL}
    """
    return conn.execute(query).fetchall()


def _fetch_all_rows(conn):
    """All non-empty messages, both senders -- for Part 2's thread view."""
    query = f"""
        SELECT {ID_COL}, {SOURCE_TYPE_COL}, {ENTITY_COL}, {SENDER_COL}, {TS_COL}, {BODY_COL}
        FROM messages
        WHERE {BODY_COL} IS NOT NULL AND TRIM({BODY_COL}) != ''
        ORDER BY {ENTITY_COL}, {TS_COL}
    """
    return conn.execute(query).fetchall()


def _source_of(conn, message_id) -> str:
    """Look up raw_json's 'source' field for one message -- composition provenance, shown as metadata only, never filtered on."""
    row = conn.execute(
        "SELECT json_extract(raw_json, '$.source') s FROM messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    return row[0] if row and row[0] else "?"


def _entity_label(row) -> str:
    return f"{row['source_type']}:{row['source_uuid'][:8]}"


def dump_candidates(conn):
    guest_rows = _fetch_guest_rows(conn)
    print(f"Total non-empty guest messages: {len(guest_rows)}\n")

    # --- 1. Keyword-clustered candidates (guest-authored only) ---
    print("=" * 70)
    print("PART 1 -- GUEST MESSAGES BY TOPIC KEYWORD (candidate queries)")
    print("=" * 70)
    for topic, kws in TOPIC_KEYWORDS.items():
        matches = [r for r in guest_rows if any(kw.lower() in (r["body"] or "").lower() for kw in kws)]
        print(f"\n--- {topic} ({len(matches)} matches) ---")
        if not matches:
            print("  (none -- keyword list may not match this corpus for this topic)")
        for row in matches[:5]:
            snippet = textwrap.shorten(_clean(row["body"]), width=140)
            print(f"  [id={row['id']}] [entity={_entity_label(row)}] {snippet}")
        if len(matches) > 5:
            print(f"  ... and {len(matches) - 5} more")

    # --- 2. Longest threads, full context (both senders, chronological) ---
    print("\n" + "=" * 70)
    print("PART 2 -- LONGEST THREADS (multi-turn context, both senders, chronological)")
    print("=" * 70)
    all_rows = _fetch_all_rows(conn)
    by_entity = defaultdict(list)
    for row in all_rows:
        by_entity[(row["source_type"], row["source_uuid"])].append(row)
    longest = sorted(by_entity.items(), key=lambda kv: -len(kv[1]))[:8]
    for (source_type, entity_uuid), msgs in longest:
        print(f"\n--- {source_type}:{entity_uuid[:8]} ({len(msgs)} messages) ---")
        for row in msgs[:6]:
            src = _source_of(conn, row["id"])
            snippet = textwrap.shorten(_clean(row["body"]), width=120)
            print(f"  [{row['sender']:5s} src={src:10s}] {snippet}")
        if len(msgs) > 6:
            print(f"  ... {len(msgs) - 6} more in this thread")

    # --- 3. Random unclustered sample (guest-authored, for long-tail queries) ---
    print("\n" + "=" * 70)
    print("PART 3 -- RANDOM SAMPLE OF GUEST MESSAGES (long-tail / expected-failure queries)")
    print("=" * 70)
    random.seed(42)
    sample = random.sample(guest_rows, min(20, len(guest_rows)))
    for row in sample:
        snippet = textwrap.shorten(_clean(row["body"]), width=140)
        print(f"  [id={row['id']}] [entity={_entity_label(row)}] {snippet}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python dump_candidates.py /path/to/hospitable.db [--schema-only]")
        sys.exit(1)

    db_path = sys.argv[1]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    dump_schema(conn)

    if "--schema-only" in sys.argv:
        sys.exit(0)

    dump_candidates(conn)
