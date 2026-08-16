#!/usr/bin/env python3
"""
dump_all_queries.py — runs verify_queries.py once per entry in queries.json
and writes all output to a single text file, each entry's section marked
with a title line like "q08====", so the whole thing can be pasted into
another chat for a triage review pass.

USAGE

  python3 scripts/dump_all_queries.py

  # Custom paths, if your layout differs from the defaults
  python3 scripts/dump_all_queries.py --queries-path data/queries.json \
      --verify-script scripts/verify_queries.py \
      --out data/queries_dump.txt

  # Pass extra flags through to verify_queries.py for every call, e.g.
  # to include full thread context for each entry
  python3 scripts/dump_all_queries.py --verify-args --show-thread

This script only reads queries.json (to get the list of ids) and shells
out to verify_queries.py per id. It does not modify queries.json or the
database.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_ids(queries_path: Path) -> list[str]:
    if not queries_path.exists():
        sys.exit(f"ERROR: queries file not found at {queries_path}")
    with open(queries_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ids = [q.get("id") for q in data.get("queries", []) if q.get("id")]
    if not ids:
        sys.exit(f"ERROR: no entries with an 'id' field found in {queries_path}")
    return ids


def run_verify(verify_script: Path, question_id: str, extra_args: list[str]) -> str:
    cmd = [sys.executable, str(verify_script), f"--questions={question_id}", *extra_args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout
    if result.stderr.strip():
        output += f"\n[stderr]\n{result.stderr}"
    if result.returncode != 0:
        output += f"\n[verify_queries.py exited with code {result.returncode}]"
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump verify_queries.py output for every entry in queries.json into one pasteable text file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--queries-path", type=Path, default=Path("data/queries.json"),
        help="Path to queries.json (default: data/queries.json)",
    )
    parser.add_argument(
        "--verify-script", type=Path, default=Path("scripts/verify_queries.py"),
        help="Path to verify_queries.py (default: scripts/verify_queries.py)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("data/queries_dump.txt"),
        help="Output file path (default: data/queries_dump.txt)",
    )
    parser.add_argument(
        "--verify-args", nargs=argparse.REMAINDER, default=[],
        help="Any remaining args are passed through to every verify_queries.py call "
             "(e.g. --verify-args --show-thread)",
    )

    args = parser.parse_args()

    ids = load_ids(args.queries_path)
    print(f"Found {len(ids)} entries: {', '.join(ids)}")

    args.out.parent.mkdir(parents=True, exist_ok=True)

    sections = []
    for qid in ids:
        print(f"  running verify_queries.py --questions={qid} ...")
        output = run_verify(args.verify_script, qid, args.verify_args)
        title = f"{qid}===="
        sections.append(f"{title}\n{output.rstrip()}\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(sections))

    print(f"\nWrote {len(ids)} sections to {args.out}")
    print("This script made no changes to queries.json or the database.")


if __name__ == "__main__":
    main()
