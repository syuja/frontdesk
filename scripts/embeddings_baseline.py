#!/usr/bin/env python3
"""
Phase 3 Step 2 -- embeddings retrieval baseline.

Measurement script, not a service: embeds guest-authored messages in
data/hospitable.db with a local sentence-transformers model, stores the
vectors as a plain numpy array, runs every entry in data/queries.json
through brute-force cosine similarity, and reports precision/recall@k
overall and by query type -- directly comparable to
scripts/bm25_baseline.py's numbers. Does not touch data/queries.json or
scripts/bm25_baseline.py. No RRF, no hybrid, no reranking -- see
STEP2_FASTFOLLOW_HYBRID.md.

Usage: uv run python3 scripts/embeddings_baseline.py
"""
from __future__ import annotations

import json
import sqlite3
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hospitable.db"
QUERIES_PATH = Path(__file__).resolve().parent.parent / "data" / "queries.json"
EMB_DIR = Path(__file__).resolve().parent.parent / "data" / "embeddings"
EMB_PATH = EMB_DIR / "corpus_embeddings.npy"
IDS_PATH = EMB_DIR / "corpus_ids.json"

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # see report for justification
K = 15  # see report for justification


def load_guest_messages(conn: sqlite3.Connection) -> list[dict]:
    """Same filter as scripts/bm25_baseline.py's load_guest_messages -- must
    produce the identical 658-message corpus for a valid comparison."""
    cur = conn.execute(
        "SELECT id, source_type, source_uuid, body FROM messages "
        "WHERE sender = 'guest' AND body IS NOT NULL AND TRIM(body) != '' "
        "ORDER BY id"
    )
    return [
        {"id": r[0], "source_type": r[1], "source_uuid": r[2], "body": r[3]}
        for r in cur.fetchall()
    ]


def build_or_load_embeddings(model: SentenceTransformer, messages: list[dict]) -> tuple[np.ndarray, float]:
    """Returns (embeddings, build_seconds). build_seconds is 0.0 if loaded from disk cache."""
    ids = [m["id"] for m in messages]
    if EMB_PATH.exists() and IDS_PATH.exists():
        cached_ids = json.loads(IDS_PATH.read_text())
        if cached_ids == ids:
            return np.load(EMB_PATH), 0.0
        print("Cached embeddings' message-id order doesn't match current corpus -- rebuilding.")

    bodies = [m["body"] for m in messages]
    t0 = time.perf_counter()
    embeddings = model.encode(
        bodies, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    )
    build_s = time.perf_counter() - t0

    EMB_DIR.mkdir(parents=True, exist_ok=True)
    np.save(EMB_PATH, embeddings)
    IDS_PATH.write_text(json.dumps(ids))
    return embeddings, build_s


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    guest_total = conn.execute("SELECT COUNT(*) FROM messages WHERE sender = 'guest'").fetchone()[0]
    host_total = conn.execute("SELECT COUNT(*) FROM messages WHERE sender = 'host'").fetchone()[0]

    messages = load_guest_messages(conn)
    print(f"Total messages in DB: {total_messages}")
    print(f"  guest: {guest_total}  host: {host_total}")
    print(f"Indexed (guest, non-empty body): {len(messages)}")

    bm25_corpus_size = 658
    if len(messages) != bm25_corpus_size:
        raise SystemExit(
            f"CORPUS MISMATCH: {len(messages)} messages loaded, expected {bm25_corpus_size} "
            f"(the BM25 baseline's corpus size). Stopping rather than proceeding with a "
            f"mismatched corpus -- see report for what changed."
        )
    print(f"Corpus size matches BM25 baseline ({bm25_corpus_size}) -- confirmed.")

    t_load0 = time.perf_counter()
    model = SentenceTransformer(MODEL_NAME)
    load_s = time.perf_counter() - t_load0
    dim = model.get_sentence_embedding_dimension()
    print(f"\nModel: {MODEL_NAME}")
    print(f"Model load time: {load_s:.2f}s")
    print(f"Embedding dimensionality: {dim}")

    tracemalloc.start()
    embeddings, build_s = build_or_load_embeddings(model, messages)
    current_b, peak_b = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if build_s > 0:
        print(f"Corpus embedding time: {build_s:.2f}s for {len(messages)} docs ({build_s/len(messages)*1000:.1f} ms/doc)")
    else:
        print(f"Loaded cached embeddings from {EMB_PATH} (delete this file to force a rebuild)")
    print(f"Embedding array shape: {embeddings.shape} dtype: {embeddings.dtype}")
    print(f"Embedding array in-memory size: {embeddings.nbytes/1024:.1f} KiB")
    print(f"Embedding array on-disk size: {EMB_PATH.stat().st_size/1024:.1f} KiB ({EMB_PATH})")
    print(f"tracemalloc during build/load call: current={current_b/1024:.1f} KiB peak={peak_b/1024:.1f} KiB")

    data = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    queries = data["queries"]

    query_texts = [q["query"] for q in queries]
    t0 = time.perf_counter()
    query_embeddings = model.encode(
        query_texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    )
    query_embed_s = time.perf_counter() - t0
    print(f"\nQuery embedding time: {query_embed_s*1000:.1f} ms for {len(query_texts)} queries "
          f"({query_embed_s/len(query_texts)*1000:.2f} ms/query)")

    results = []
    for q, q_emb in zip(queries, query_embeddings):
        # embeddings are L2-normalized, so dot product == cosine similarity
        scores = embeddings @ q_emb
        ranked_idx = np.argsort(-scores)[:K]
        top_k_ids = [messages[i]["id"] for i in ranked_idx]
        top_k_scores = [float(scores[i]) for i in ranked_idx]

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

    Path("/tmp/embeddings_results.json").write_text(
        json.dumps(
            [{**r, "expected_ids": list(r["expected_ids"]), "hits": list(r["hits"])} for r in results],
            indent=2,
        )
    )
    print("\nWrote raw per-entry results to /tmp/embeddings_results.json")

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
            print(f"{r['id']}: top_score={r['top_score']:.4f}  top_msg_id={r['top_k_ids'][0] if r['top_k_ids'] else None}")

    print("\n--- per-entry detail ---")
    for r in results:
        p = f"{r['precision']:.3f}" if r["precision"] is not None else "n/a"
        rc = f"{r['recall']:.3f}" if r["recall"] is not None else "n/a"
        print(f"{r['id']} [{r['type']}] P={p} R={rc} hits={sorted(r['hits'])} expected={sorted(r['expected_ids'])} top_score={r['top_score']:.4f}")


if __name__ == "__main__":
    main()
