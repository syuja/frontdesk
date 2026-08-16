#!/usr/bin/env python3
"""
Phase 3 Step 2 -- BM25 retrieval baseline.

Measurement script, not a service: builds an in-memory BM25Okapi index over
guest-authored messages in data/hospitable.db, runs every entry in
data/queries.json through it, and reports precision/recall@k overall and by
query type. Does not touch data/queries.json. No embeddings, no RRF, no
reranking -- see STEP2_FASTFOLLOW_HYBRID.md for why those are out of scope
here.

Usage: uv run python3 scripts/bm25_baseline.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

import nltk
from rank_bm25 import BM25Okapi

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hospitable.db"
QUERIES_PATH = Path(__file__).resolve().parent.parent / "data" / "queries.json"

K = 15  # see report for justification

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stopwords() -> set[str]:
    try:
        from nltk.corpus import stopwords
        return set(stopwords.words("english"))
    except LookupError:
        nltk.download("stopwords", quiet=True)
        from nltk.corpus import stopwords
        return set(stopwords.words("english"))


STOPWORDS = _stopwords()


def preprocess(text: str) -> list[str]:
    """Lowercase, strip punctuation, tokenize, drop stopwords."""
    text = text.lower()
    tokens = _TOKEN_RE.findall(text)
    return [t for t in tokens if t not in STOPWORDS]


def load_guest_messages(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT id, source_type, source_uuid, body FROM messages "
        "WHERE sender = 'guest' AND body IS NOT NULL AND TRIM(body) != '' "
        "ORDER BY id"
    )
    return [
        {"id": r[0], "source_type": r[1], "source_uuid": r[2], "body": r[3]}
        for r in cur.fetchall()
    ]


def entity_string(msg: dict) -> str:
    return f"{msg['source_type']}:{msg['source_uuid'][:8]}"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    guest_total = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE sender = 'guest'"
    ).fetchone()[0]
    host_total = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE sender = 'host'"
    ).fetchone()[0]

    messages = load_guest_messages(conn)
    print(f"Total messages in DB: {total_messages}")
    print(f"  guest: {guest_total}  host: {host_total}")
    print(f"Indexed (guest, non-empty body): {len(messages)}")

    # Preprocessing examples
    print("\n--- tokenization examples ---")
    for msg in messages[:1] + messages[300:301] + messages[600:601]:
        toks = preprocess(msg["body"])
        print(f"id={msg['id']} BEFORE: {msg['body']!r}")
        print(f"id={msg['id']} AFTER:  {toks}")

    corpus_tokens = [preprocess(m["body"]) for m in messages]

    tracemalloc.start()
    t0 = time.perf_counter()
    bm25 = BM25Okapi(corpus_tokens)
    build_s = time.perf_counter() - t0
    current_b, peak_b = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"\nIndex build time: {build_s*1000:.1f} ms for {len(messages)} docs")
    print(f"Index memory (tracemalloc, BM25Okapi() call only): current={current_b/1024:.1f} KiB peak={peak_b/1024:.1f} KiB")

    data = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    queries = data["queries"]

    results = []
    for q in queries:
        q_tokens = preprocess(q["query"])
        scores = bm25.get_scores(q_tokens)
        ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        top_k = ranked_idx[:K]
        top_k_ids = [messages[i]["id"] for i in top_k]
        top_k_scores = [scores[i] for i in top_k]

        expected = q["expected"]
        expected_ids = {e["message_id"] for e in expected}

        retrieved_hits = expected_ids & set(top_k_ids)
        precision = len(retrieved_hits) / K if expected_ids else None
        recall = (len(retrieved_hits) / len(expected_ids)) if expected_ids else None

        results.append({
            "id": q["id"],
            "type": q["type"],
            "query": q["query"],
            "expected_ids": expected_ids,
            "top_k_ids": top_k_ids,
            "top_k_scores": top_k_scores,
            "hits": retrieved_hits,
            "precision": precision,
            "recall": recall,
            "top_score": top_k_scores[0] if top_k_scores else 0.0,
        })

    Path("/tmp/bm25_results.json").write_text(
        json.dumps(
            [{**r, "expected_ids": list(r["expected_ids"]), "hits": list(r["hits"])} for r in results],
            indent=2,
        )
    )
    print("\nWrote raw per-entry results to /tmp/bm25_results.json")

    # by-type aggregation (excluding true_negative, no precision/recall defined)
    by_type = defaultdict(list)
    for r in results:
        if r["type"] != "true_negative":
            by_type[r["type"]].append(r)

    print("\n--- precision/recall@%d by type ---" % K)
    all_p, all_r = [], []
    for t, rs in by_type.items():
        ps = [r["precision"] for r in rs]
        rcs = [r["recall"] for r in rs]
        all_p.extend(ps)
        all_r.extend(rcs)
        print(f"{t:15s} n={len(rs):2d}  avg_precision@{K}={sum(ps)/len(ps):.3f}  avg_recall@{K}={sum(rcs)/len(rcs):.3f}")
    print(f"{'OVERALL':15s} n={len(all_p):2d}  avg_precision@{K}={sum(all_p)/len(all_p):.3f}  avg_recall@{K}={sum(all_r)/len(all_r):.3f}")

    print("\n--- true_negative top scores (no correct answer exists) ---")
    for r in results:
        if r["type"] == "true_negative":
            print(f"{r['id']}: top_score={r['top_score']:.3f}  top_msg_id={r['top_k_ids'][0] if r['top_k_ids'] else None}")

    print("\n--- per-entry detail ---")
    for r in results:
        p = f"{r['precision']:.3f}" if r["precision"] is not None else "n/a"
        rc = f"{r['recall']:.3f}" if r["recall"] is not None else "n/a"
        print(f"{r['id']} [{r['type']}] P={p} R={rc} hits={sorted(r['hits'])} expected={sorted(r['expected_ids'])} top_score={r['top_score']:.3f}")


if __name__ == "__main__":
    main()
