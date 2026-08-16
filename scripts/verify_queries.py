#!/usr/bin/env python3
"""
verify_queries.py — human review tool for data/queries.json

Prints the actual message content (and optionally full source threads) for
entries in queries.json, so a human can read the query next to its labeled
answer(s) and judge correctness/completeness directly, without trusting a
verification pass blind.

This script is READ-ONLY. It never modifies queries.json or hospitable.db.

USAGE

  # Show everything
  python3 scripts/verify_queries.py

  # Show one or more specific entries by id
  python3 scripts/verify_queries.py --questions q01,q07,q13

  # Show only entries matching a complete status
  python3 scripts/verify_queries.py --complete=false
  python3 scripts/verify_queries.py --complete=true

  # Combine: only the false ones, but just q07 and q09
  python3 scripts/verify_queries.py --complete=false --questions=q07,q09

  # For any shown entry whose expected messages belong to a
  # reservation/inquiry thread, also print the full thread in
  # chronological order (not just the labeled message)
  python3 scripts/verify_queries.py --questions=q01 --show-thread

  # Corpus-wide keyword search — for q03-style coverage-gap checks,
  # independent of what's currently in queries.json. Searches guest
  # (non-host) message bodies for a substring, case-insensitive.
  python3 scripts/verify_queries.py --search "early check"
  python3 scripts/verify_queries.py --search "check in" --search "check-in"

  # Limit search to guest messages only (default) or include host/automated
  python3 scripts/verify_queries.py --search "wifi" --include-host

PATHS

  Defaults assume you run this from the repo root:
    data/queries.json
    data/hospitable.db

  Override with --queries-path / --db-path if your layout differs.
"""

import argparse
import json
import sqlite3
import sys
import textwrap
from pathlib import Path


def load_queries(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"ERROR: queries file not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def connect_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"ERROR: database not found at {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def wrap(text: str, width: int = 100, indent: str = "        ") -> str:
    if not text:
        return indent + "(empty body)"
    lines = textwrap.wrap(text, width=width)
    return "\n".join(f"{indent}{line}" for line in lines) or indent + "(empty body)"


def fetch_message(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, source_type, source_uuid, sender, body, created_at, content_type "
        "FROM messages WHERE id = ?",
        (message_id,),
    ).fetchone()


def fetch_thread(conn: sqlite3.Connection, entity: str) -> list[sqlite3.Row]:
    """entity is 'reservation:UUID' or 'inquiry:UUID'."""
    if ":" not in entity:
        return []
    source_type, source_uuid = entity.split(":", 1)
    return conn.execute(
        "SELECT id, source_type, source_uuid, sender, body, created_at, content_type "
        "FROM messages WHERE source_type = ? AND source_uuid = ? "
        "ORDER BY created_at ASC, id ASC",
        (source_type, source_uuid),
    ).fetchall()


def print_message_row(row: sqlite3.Row, mark: str = "  ") -> None:
    print(f"{mark} [id={row['id']}] {row['source_type']}:{row['source_uuid']} "
          f"sender={row['sender']} content_type={row['content_type']} "
          f"created_at={row['created_at']}")
    print(wrap(row["body"]))


def print_entry(entry: dict, conn: sqlite3.Connection, show_thread: bool) -> None:
    print("=" * 100)
    print(f"{entry['id']}  [{entry.get('type', '?')}]  complete={entry.get('complete')}")
    print(f"QUERY: {entry.get('query', '')}")
    note = entry.get("note", "")
    if note:
        print("NOTE:")
        print(wrap(note, indent="    "))

    expected = entry.get("expected", [])
    if not expected:
        print("\n  expected: [] (true negative, or not yet populated)")
        return

    print(f"\n  expected ({len(expected)} message(s)):")
    seen_entities: set[str] = set()
    missing_ids: list[int] = []

    for exp in expected:
        mid = exp.get("message_id")
        entity = exp.get("entity", "")
        row = fetch_message(conn, mid) if mid is not None else None
        if row is None:
            print(f"  [id={mid}] {entity}  *** NOT FOUND IN DB ***")
            missing_ids.append(mid)
            continue
        # flag if the stored entity disagrees with the DB's own record —
        # this is the exact class of bug the prior pass caught on msg 91
        # (labeled reservation:ab600c9c, actually inquiry:ab600c9c).
        #
        # queries.json entities use this project's established 8-char UUID
        # prefix convention (e.g. "reservation:07fae9b7"), not the full
        # UUID -- compare against both forms so a correctly-truncated
        # label isn't falsely flagged as a mismatch. A genuine error (wrong
        # source_type, or a UUID that doesn't match either form) still is.
        db_entity = f"{row['source_type']}:{row['source_uuid']}"
        db_entity_short = f"{row['source_type']}:{row['source_uuid'][:8]}"
        mark = "  "
        if entity and entity not in (db_entity, db_entity_short):
            mark = "!!"
            print(f"{mark} [id={mid}] LABEL SAYS entity={entity!r} "
                  f"BUT DB SAYS entity={db_entity!r} -- MISMATCH")
        print_message_row(row, mark=mark)
        seen_entities.add(db_entity)
        print()

    if missing_ids:
        print(f"  *** {len(missing_ids)} expected message_id(s) not found in DB: {missing_ids} ***")

    if show_thread:
        for entity in sorted(seen_entities):
            thread = fetch_thread(conn, entity)
            if not thread:
                continue
            expected_ids = {e.get("message_id") for e in expected}
            print(f"\n  --- full thread {entity} ({len(thread)} messages) ---")
            for row in thread:
                mark = "* " if row["id"] in expected_ids else "  "
                print_message_row(row, mark=mark)
            print()


def do_search(conn: sqlite3.Connection, terms: list[str], include_host: bool) -> None:
    print("=" * 100)
    print(f"CORPUS SEARCH: terms={terms!r}  include_host={include_host}")
    print("=" * 100)

    clauses = " OR ".join("LOWER(body) LIKE ?" for _ in terms)
    params = [f"%{t.lower()}%" for t in terms]

    sql = f"SELECT id, source_type, source_uuid, sender, body, created_at FROM messages WHERE ({clauses})"
    if not include_host:
        # sender only ever takes 'guest' or 'host' in this DB -- 'automated'/
        # 'hospitable'/'AI' are values of raw_json's 'source' field (host-reply
        # composition provenance), a different column, never of sender.
        sql += " AND sender != 'host'"
    sql += " ORDER BY created_at ASC, id ASC"

    rows = conn.execute(sql, params).fetchall()
    print(f"\n{len(rows)} matching message(s):\n")
    for row in rows:
        print(f"  [id={row['id']}] {row['source_type']}:{row['source_uuid']} "
              f"sender={row['sender']} created_at={row['created_at']}")
        print(wrap(row["body"]))
        print()


def parse_id_list(raw: str) -> set[str]:
    """Accepts 'q01,q07' or 'q1, q7' etc. Normalizes to zero-padded q## form
    where the numeric part is 1-2 digits, matching the ids used in queries.json."""
    out = set()
    for tok in raw.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if not tok.startswith("q"):
            tok = "q" + tok
        digits = "".join(ch for ch in tok if ch.isdigit())
        if digits:
            out.add(f"q{int(digits):02d}")
        else:
            out.add(tok)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Human review tool for queries.json — read-only, prints DB content for eval entries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--queries-path", type=Path, default=Path("data/queries.json"),
        help="Path to queries.json (default: data/queries.json)",
    )
    parser.add_argument(
        "--db-path", type=Path, default=Path("data/hospitable.db"),
        help="Path to the SQLite DB (default: data/hospitable.db)",
    )
    parser.add_argument(
        "--questions", type=str, default=None,
        help="Comma-separated list of entry ids to show, e.g. q01,q07,q13 (also accepts q1,q7,q13)",
    )
    parser.add_argument(
        "--complete", type=str, choices=["true", "false"], default=None,
        help="Filter to entries with this complete value",
    )
    parser.add_argument(
        "--show-thread", action="store_true",
        help="Also print the full source thread (chronological) for each shown entry, "
             "with expected messages marked '*'",
    )
    parser.add_argument(
        "--search", action="append", default=None,
        help="Corpus-wide substring search (case-insensitive) over message bodies, independent "
             "of queries.json. Repeatable: --search 'early check' --search 'check-in' ORs the terms.",
    )
    parser.add_argument(
        "--include-host", action="store_true",
        help="With --search, include host/automated/AI-sender messages (default: guest messages only)",
    )

    args = parser.parse_args()

    conn = connect_db(args.db_path)

    if args.search:
        do_search(conn, args.search, args.include_host)
        return

    data = load_queries(args.queries_path)
    entries = data.get("queries", [])

    if args.questions:
        wanted_ids = parse_id_list(args.questions)
        entries = [e for e in entries if e.get("id") in wanted_ids]
        found_ids = {e.get("id") for e in entries}
        missing = wanted_ids - found_ids
        if missing:
            print(f"WARNING: no entry found for: {sorted(missing)}", file=sys.stderr)

    if args.complete is not None:
        want = args.complete == "true"
        entries = [e for e in entries if bool(e.get("complete")) == want]

    if not entries:
        print("No matching entries.")
        return

    print(f"Showing {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}\n")
    for entry in entries:
        print_entry(entry, conn, show_thread=args.show_thread)

    print("=" * 100)
    print(f"Done. {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} shown. "
          f"This script made no changes to {args.queries_path} or {args.db_path}.")


if __name__ == "__main__":
    main()
