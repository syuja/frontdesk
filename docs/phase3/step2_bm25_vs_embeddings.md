# Phase 3 Step 2 — BM25 vs. Embeddings: the measured comparison

This is the deliverable `PHASE3_PLAN.md` names as the actual Step 2
product — "implement both, measure retrieval quality on real queries, and
publish the comparison." Full detail on each method lives in its own
report; this document exists so the comparison itself is visible in one
place.

- BM25 baseline: `docs/phase3/step2_bm25_baseline.md` /
  `scripts/bm25_baseline.py`
- Embeddings baseline: `docs/phase3/step2_embeddings_baseline.md` /
  `scripts/embeddings_baseline.py`

**Same corpus (658 guest messages, confirmed identical — the embeddings
script hard-stops on a mismatch), same eval set (`data/queries.json`,
26 entries), same k (15).** Retrieval method is the only variable.

## Headline numbers

| type | n | BM25 recall@15 | Embeddings recall@15 | Δ recall | BM25 precision@15 | Embeddings precision@15 |
|---|---|---|---|---|---|---|
| lexical | 7 | **0.771** | 0.671 | −0.100 | **0.152** | 0.133 |
| paraphrase | 10 | 0.346 | **0.588** | **+0.242** | 0.053 | **0.087** |
| multi_turn | 4 | 0.100 | **0.325** | **+0.225** | 0.033 | **0.100** |
| cross_lingual | 2 | 0.000 | **1.000** | **+1.000** | 0.000 | **0.067** |
| **overall** | 23 | 0.403 | **0.603** | **+0.200** | 0.075 | **0.101** |

(multi_turn recall is computed the same way as every other entry — hits
in top-k divided by the FULL `expected` array length, host messages
included. q18–q20's `expected` arrays include host-authored messages that
are excluded from both guest-only indexes, so those three entries can
never reach recall 1.0 no matter how well retrieval performs; the lowered
ceiling (0.556 / 0.600 / 0.400) is context for *interpreting* the reported
number, not an adjustment applied to it. See either report's Corpus
section.)

**Bottom line:** embeddings win, clearly, and not marginally. Overall
recall@15 improves by 20 points (0.403 → 0.603), driven by exactly the
three types the eval set was built to stress on wording/language
mismatch (paraphrase, multi_turn, cross_lingual), while giving up only 10
points on lexical — the one type BM25 has a structural right to win.
`PHASE3_PLAN.md` left open the possibility that "BM25 may well be
competitive" at this corpus size; on this measurement, for this
query mix, it isn't — it wins narrowly on its best type and loses
substantially everywhere else.

## Where embeddings clearly help

**cross_lingual (0.000 → 1.000)** — the cleanest result in the whole
comparison. BM25 has no mechanism to match English query tokens against
Spanish/Czech message tokens at all; the chosen embedding model
(`paraphrase-multilingual-MiniLM-L12-v2`) is trained across 50+ languages
including both, and hit a clean recall of 1.0 on both q22 (Spanish) and
q23 (Czech) with no translation step in the pipeline. Note this also
isolates a variable from the BM25 report: BM25's Czech failure had a
tokenization-damage bug compounding the language gap (accented characters
fragmenting into meaningless pieces — see the BM25 report's "Known
limitation, deferred" note). Embeddings have no equivalent tokenization
step and still succeeded, confirming the *language* gap was the real
limiter for BM25, not primarily the tokenizer bug riding along on top of
it.

**paraphrase (0.346 → 0.588)** — two complete BM25 misses became clean
hits: q14 ("listing photos don't match" vs. the message's "pics," never
"photos") and q15 ("thermostat...cooling" vs. the message's "heat/air,"
never "thermostat" or "cooling"). Both are synonym/category gaps with
literally zero shared content words — textbook cases for semantic
matching over keyword matching.

**multi_turn — one entry in particular (q21: kitchen equipment)** — BM25
scored a clean 0.000 (query asks about "kitchen equipment or cooking
supplies" in the abstract; messages ask for concrete items like "a baking
sheet" or "aluminum foil," zero shared vocabulary). Embeddings recovered
0.500. This is the single most direct demonstration in the eval set of
category-to-instance generalization — the thing embeddings are supposed
to buy over pure keyword overlap.

## Where embeddings don't meaningfully help, or trade something away

**lexical (0.771 → 0.671, embeddings behind)** — expected direction, not
a concern. BM25 is a keyword-overlap scorer and this type is
near-verbatim wording by construction — its best possible case. Losing
10 points here while gaining far more elsewhere is the trade the eval set
was designed to reveal, not a red flag.

**q10 — a genuine embeddings regression worth naming plainly.** BM25 got
recall 0.667 here ("guest can't get in with the door code"); embeddings
got **0.000**, despite a high top similarity score (0.7088). Investigated
directly (see the embeddings report's per-type commentary): the three
expected messages are short fragments from one specific reservation
thread ("the code is not working" / "but code is wrong" / "we are trying
to get into the house"). The top-15 embedding results instead surfaced a
dense cluster of near-identical access-failure complaints from **other**
reservations — several of which are the exact incidents q11 (a separate
entry) was built to test. This is a real, distinct failure mode from
anything BM25 exhibits: semantic similarity correctly identifies "this is
a door/code complaint" as a topic but can blur *which specific incident*
a query is about when a corpus has several near-duplicate short
complaints on the same broad topic — something BM25's exact-phrase
matching handled better precisely because it isn't generalizing. Worth
flagging as a concrete counter-example to "more semantic is strictly
better" for the writeup.

**Similarity-score separation (true negatives) is meaningfully worse.**
BM25's true-negative top scores sat at ~21–25% of its best genuine-match
score (4.71–5.72 vs. 22.49) — a wide, usable gap. Embeddings' true-negative
scores sat at ~53–66% of its best genuine-match score (0.41–0.52 vs.
0.78), and **two of the three true-negative scores fell inside the
observed range of genuine low-confidence matches**, not below it (see the
embeddings report's true_negative section for the exact numbers). If a
future system wants a confidence-threshold cutoff ("don't surface
anything below score X"), BM25's raw scores would support a much cleaner
threshold than cosine similarity does here — this is a real design
constraint the embeddings approach introduces, not a minor footnote.

## Infrastructure and cost tradeoffs

| | BM25 | Embeddings |
|---|---|---|
| New runtime dependency | `rank-bm25` (pure Python) | `sentence-transformers` + `torch` |
| One-time disk footprint | negligible | **458 MB** (model weights, cached locally) |
| Index/vector build time (658 docs) | 5.8 ms | 3.27 s (+ 2.65 s model load) |
| Stored index/vector size | ~390 KiB (in-memory) | ~987 KiB (`.npy`, on disk) |
| Per-query cost | near-zero (term lookup) | ~2.56 ms/query (model forward pass) |
| Data leaves the machine? | no | no (local model, confirmed by design choice) |
| Needs a paid API? | no | no |

At this corpus size (658 docs), every cost on both sides is trivial in
absolute terms — sub-4-second builds, sub-3ms queries, sub-1MB vector
storage either way. The one cost that isn't trivial is the 458 MB model
download/cache, a new one-time operational footprint BM25 simply doesn't
have. None of these costs are large enough to be a deciding factor at
current corpus size; worth re-measuring if the corpus grows by an order
of magnitude or more (brute-force cosine similarity over a numpy array is
the first thing that would need revisiting, per the reasoning already in
`PHASE3_PLAN.md` for skipping a managed vector store now).

## Recommendation for the writeup

Lead with the paraphrase and cross_lingual deltas — they're large,
specific, and each has a clean causal story (vocabulary gap, language
gap) rather than being a diffuse "embeddings are better" claim. Use q10
as the honest counterweight: embeddings are not strictly better, and the
one clear regression has a specific, explainable mechanism worth walking
through rather than omitting. The true-negative separation finding is the
right note to end on for anyone planning to build a confidence threshold
on top of either approach — it's a concrete, measured reason to not
assume cosine similarity behaves like BM25 scores just because both
produce "a number per document."

## Scope confirmation

- `data/queries.json` and `scripts/bm25_baseline.py` were not modified.
- No RRF, hybrid combination, or reranking implemented — per
  `STEP2_FASTFOLLOW_HYBRID.md`, this comparison is exactly the artifact
  that fast-follow is sequenced after.
- Stopping here for review, per instruction.
