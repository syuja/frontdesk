# Fast-follow — Hybrid Retrieval + Reranking

> Captured during Step 2 planning. Explicit decision: **not in Step 2 scope.**
> Step 2 ships BM25 vs. embeddings, measured independently, baseline published first.
> This is committed as the next thing after that baseline lands — not optional, not
> a maybe. Corpus is expected to grow, and hybrid is the target end-state.

---

## What's being deferred, and why

Step 2 measures BM25 and embeddings **separately** against the labeled query set
(`data/eval/queries.json`) and ships whichever wins, or both independently if the
writeup calls for it. No merging, no reranker, in that scope.

**Why not now:** the entire point of Step 2 is producing a clean number — does
semantic search actually beat keyword search on this corpus, and by how much.
Adding a reranker before that baseline exists means never getting an honest
answer to that question; you'd be measuring "hybrid+reranker" against nothing.
Same discipline as pre-committing eval thresholds before seeing output — measure
the simple thing first, then measure what the complex thing buys you on top.

**Why it's still committed, not shelved:** corpus is expected to grow beyond
~1,454 messages, and hybrid search is the stated target architecture. This
isn't "maybe later" — it's the plan, sequenced after the baseline so the
before/after comparison is real.

---

## What ships in the fast-follow

1. **Combine BM25 + embedding results via RRF** (Reciprocal Rank Fusion) —
   merges two ranked lists without needing to normalize BM25 scores against
   cosine similarities, which aren't on the same scale.
2. **Rerank the merged candidates** — options to evaluate, not pre-decided:
   - A small/cheap model (fast, low-cost second pass over the merged top-k)
   - Defer reranking initially, ship RRF-only hybrid first, add reranking as
     its own measured increment if RRF alone doesn't close the gap
3. **Measure against the same `queries.json` baseline** from Step 2 — the
   whole value of this fast-follow is showing the delta: BM25 alone vs.
   embeddings alone vs. hybrid+RRF vs. hybrid+RRF+rerank, same labeled set,
   same metric (precision/recall @k).

## Why this belongs in the writeup

Step 2's blog post measures BM25 vs. embeddings honestly, including the case
where BM25 holds its own. This fast-follow is the natural next section:
"here's what hybrid + reranking bought on top of the baseline, measured the
same way." That framing — baseline first, complexity justified by a measured
delta — is the stronger signal than shipping hybrid+rerank from day one and
never knowing what each piece contributed.

## Scope guard

Do not pull this into Step 2. If corpus growth or query difficulty creates
pressure to add hybrid mid-Step-2, that pressure is exactly the named
scope-creep pattern (new idea arriving mid-build) — the answer is finishing
Step 2's baseline and sequencing this after, not folding it in.
