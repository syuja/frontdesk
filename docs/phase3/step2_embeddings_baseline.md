# Phase 3 Step 2 — Embeddings Retrieval Baseline

Measurement task per `PHASE3_PLAN.md` (Step 2 — Retrieval layer), directly
comparable to `docs/phase3/step2_bm25_baseline.md`. Deliverable is a
precision/recall report against `data/queries.json`, not a production
retrieval service. `data/queries.json` and `scripts/bm25_baseline.py` were
not modified.

## Corpus — confirmed identical to BM25

Same filter as `scripts/bm25_baseline.py`: `sender = 'guest' AND body IS
NOT NULL AND TRIM(body) != ''`, from `data/hospitable.db`'s `messages`
table (1,467 total: 796 host, 671 guest).

**658 messages indexed** — the script checks this against the BM25
baseline's known corpus size before doing anything else and hard-stops on
a mismatch (`CORPUS MISMATCH` + `SystemExit`) rather than silently
proceeding. Confirmed match, no mismatch encountered. This corpus carries
forward the same caveat established in the BM25 report: `multi_turn`
entries q18–q20 have `expected` arrays that include host-authored
messages not present in this guest-only index, so their recall ceiling is
below 1.0 (0.556 / 0.600 / 0.400 respectively) — reported against that
ceiling below, not against 1.0.

## Model

**`paraphrase-multilingual-MiniLM-L12-v2`** (sentence-transformers,
local, CPU inference — no hosted API, no new secret, guest text never
leaves the machine).

**Why this model, not `all-MiniLM-L6-v2`:** the corpus contains genuine
non-English content (Spanish q22, Czech q23), and the whole point of a
"can embeddings bridge cross-lingual gaps" test is defeated by an
English-only model — `all-MiniLM-L6-v2` would score ~0 on q22/q23 for a
different reason than BM25 did (no multilingual training, not just no
translation step), producing a misleading "embeddings don't help
cross-lingual" conclusion. This model is trained specifically for
cross-lingual semantic similarity across 50+ languages (including both
Spanish and Czech) using a paraphrase-mining objective, which also fits
the `paraphrase`-type queries well — matching queries to differently-worded
messages with the same intent is literally its training objective, not
just short/informal chat text tolerance.

- **Dimensionality:** 384 (matches `all-MiniLM-L6-v2`'s size class — this
  is the multilingual sibling in the same "small, fast MiniLM" family, not
  a step up to a heavier model).
- **Fit for short, informal chat text:** paraphrase-mining training data
  is short-sentence-pair-oriented, which matches guest messages better
  than models trained primarily on long-document retrieval.
- **CPU inference speed at this corpus size:** measured directly, not
  assumed — see Build section. 3.27s to embed the full 658-message corpus
  on CPU is a non-issue at this scale.

## Storage

Plain numpy array (`data/embeddings/corpus_embeddings.npy`), L2-normalized
float32 vectors, so cosine similarity reduces to a single dot product
(`embeddings @ query_embedding`) — brute-force over 658×384 floats is
well under a millisecond, no compiled vector-index extension needed, same
reasoning `PHASE3_PLAN.md` already applies to skipping a managed vector
DB at this scale. A parallel `corpus_ids.json` records message-id order so
a re-run can verify the cache still matches the current corpus before
reusing it (rebuilds automatically if not — the same corpus-integrity
discipline as the top-level mismatch check).

## Build

- **Model load time:** 2.65s (steady state, model weights already
  downloaded). One-time cost not incurred by BM25 at all: the model
  weights occupy **458 MB** on disk in the local HuggingFace cache
  (`~/.cache/huggingface/hub/...`), downloaded once on first run. This is
  the clearest new infrastructure cost embeddings introduce that BM25
  simply doesn't have — worth naming explicitly since the BM25 report's
  build cost was "5.8ms, no new dependency beyond a pure-Python package."
- **Corpus embedding time:** 3.27s for 658 documents (5.0 ms/doc), CPU only.
- **Stored vector footprint:** shape `(658, 384)`, float32 → **987.0 KiB**
  in memory and on disk (`corpus_embeddings.npy`) — essentially identical
  in size to BM25's ~390 KiB peak index memory; the storage cost of the
  two approaches is a non-issue either way at this corpus size. The real
  cost difference is the 458 MB model weights, not the resulting vectors.
- **Query embedding time:** 66.6 ms for all 26 eval queries (2.56 ms/query)
  once the model is loaded. Negligible per-query, but non-zero in a way
  BM25's pure term-lookup scoring isn't — relevant if this ever runs
  per-request rather than batched.

## Eval harness and choice of k

**k = 15 — kept identical to BM25, for comparability.**

This is a direct call: the task this comparison serves is isolating
retrieval *method* as the only variable, and changing k between the two
runs would confound "embeddings scored differently" with "the two runs
used different retrieval windows." The same underlying justification from
the BM25 report still holds unchanged — the expected-set-size range is
identical (1 to 10 messages), and k=15 sits comfortably above the max (10)
with headroom for imperfect ranking while not being so large that
precision@k becomes meaningless. Same caveat also carries over unchanged:
precision@15 is structurally capped for the many 1–2-expected entries
(e.g. a single-expected-message entry caps at 1/15 ≈ 0.067 even with a
perfect top-1 hit) — **recall@k is the metric to trust** for those, same
as BM25.

If this baseline were being tuned in isolation (not for comparison),
embeddings' smoother, less sharply peaked score distribution (see
true-negative section below) might argue for a different k — noted here,
not acted on, since comparability was the deciding factor for this pass.

For `true_negative` entries (q24–q26), precision/recall are undefined —
reported separately as the top-ranked result's cosine similarity.

## Results

### Overall and by type

| type | n | avg precision@15 | avg recall@15 |
|---|---|---|---|
| lexical | 7 | 0.133 | 0.671 |
| paraphrase | 10 | 0.087 | 0.588 |
| multi_turn | 4 | 0.100 | 0.325 |
| cross_lingual | 2 | 0.067 | 1.000 |
| **overall** (excl. true_negative) | 23 | 0.101 | 0.603 |

### true_negative — top score check, and a flag

| id | query | top score (cosine) | top message |
|---|---|---|---|
| q24 | pets/dogs allowed? | 0.4758 | msg 139 |
| q25 | smoke detector / fire safety? | 0.4110 | msg 311 |
| q26 | pool at the property? | 0.5181 | msg 656 |

**Flag, as instructed:** these are *not* cleanly separated from genuine
low-confidence matches. Across the 23 non-true-negative entries, top
scores ranged from **0.4685** (q14) to **0.7805** (q13). Two of the three
true-negative scores fall **inside** that genuine-match range, not below
it: q24 (0.4758) sits between q14 (0.4685) and q21 (0.4961); q26 (0.5181)
sits between q08 (0.4971) and q22 (0.5637). Only q25 (0.4110) sits clearly
below the genuine-match floor. A fixed similarity-threshold cutoff (e.g.
"treat anything below 0.5 as no-match") would misclassify some of these —
either flagging real low-confidence matches as negatives or accepting a
true negative as a match, depending on where the line is drawn.

This is the expected, named tradeoff, not a surprise: cosine similarity
between two normalized embeddings of any two pieces of real English (or
Spanish/Czech) guest-service text is rarely near zero — shared domain
vocabulary ("guest," "Samir," "room," short polite phrasing) pulls
everything into a compressed middle band, unlike BM25's raw term-overlap
scores, which can be near-zero when literally no terms are shared. For
direct comparison: BM25's true-negative scores (4.71 / 5.72 / 4.71) sat at
roughly **21%** of its highest genuine score (22.49, q13); this model's
true-negative scores sit at roughly **53–66%** of its highest genuine
score (0.7805, q13). That's a real, measurable drop in separation, not
just a difference in scale — worth carrying into any future
threshold-based filtering design (e.g. "no result above X counts as a
match"), which would need a considerably more conservative threshold
here than the BM25 numbers might suggest.

### Per-entry detail

| id | type | P@15 | R@15 | n_expected | top score |
|---|---|---|---|---|---|
| q01 | lexical | 0.133 | 0.667 | 3 | 0.7313 |
| q02 | lexical | 0.067 | 1.000 | 1 | 0.6073 |
| q03 | lexical | 0.267 | 0.400 | 10 | 0.7143 |
| q04 | lexical | 0.267 | 0.800 | 5 | 0.6488 |
| q05 | lexical | 0.067 | 0.333 | 3 | 0.7017 |
| q06 | lexical | 0.067 | 1.000 | 1 | 0.7327 |
| q07 | lexical | 0.067 | 0.500 | 2 | 0.5646 |
| q08 | paraphrase | 0.067 | 0.500 | 2 | 0.4971 |
| q09 | paraphrase | 0.133 | 0.667 | 3 | 0.5655 |
| q10 | paraphrase | 0.000 | 0.000 | 3 | 0.7088 |
| q11 | paraphrase | 0.200 | 0.375 | 8 | 0.7031 |
| q12 | paraphrase | 0.067 | 0.333 | 3 | 0.6594 |
| q13 | paraphrase | 0.067 | 1.000 | 1 | 0.7805 |
| q14 | paraphrase | 0.067 | 1.000 | 1 | 0.4685 |
| q15 | paraphrase | 0.133 | 1.000 | 2 | 0.6669 |
| q16 | paraphrase | 0.067 | 0.500 | 2 | 0.7177 |
| q17 | paraphrase | 0.067 | 0.500 | 2 | 0.6160 |
| q18 | multi_turn | 0.000 | 0.000 | 9 (5 indexable) | 0.5754 |
| q19 | multi_turn | 0.200 | 0.600 | 5 (3 indexable) | 0.5956 |
| q20 | multi_turn | 0.067 | 0.200 | 5 (2 indexable) | 0.6129 |
| q21 | multi_turn | 0.133 | 0.500 | 4 (4 indexable) | 0.4961 |
| q22 | cross_lingual | 0.067 | 1.000 | 1 | 0.5637 |
| q23 | cross_lingual | 0.067 | 1.000 | 1 | 0.7151 |

## Per-type commentary

**lexical (avg recall 0.671)** — strong, though slightly *below* BM25's
0.771 on this type (see comparison doc). Expected direction of travel:
BM25 is a keyword-overlap scorer and lexical queries are its best case by
construction, so embeddings holding within ~10 points of it, not beating
it, is the sane outcome — this type is not where embeddings were expected
to add value.

**paraphrase (avg recall 0.588, up from BM25's 0.346)** — the clearest
"embeddings did their job" type, as designed. q15 (thermostat) and q14
(listing photos) — both **complete BM25 misses** (recall 0.0), because
the query and message shared essentially no literal vocabulary ("photos"
vs. "pics," "thermostat/cooling" vs. "heat/air") — both hit **recall
1.000** here. This is exactly the vocabulary-mismatch gap embeddings are
meant to close.

**One real regression worth naming: q10** ("can't get in with the door
code") dropped from BM25's recall 0.667 to **0.000** here, despite a high
top score (0.7088 — one of the higher scores in the whole run, so this
isn't a case of general low confidence). Investigated directly: q10's
three expected messages (1206 "the code is not working," 1213 "but code
is wrong," 1214 "we are trying to get into the house" — all short
fragments from one specific reservation thread) are absent from the
top-15 entirely. What *is* in the top-15 instead is a dense cluster of
semantically near-identical access-failure messages from **other**
reservations — several of which are the exact incidents q11 was
separately built to test (491, 489, 894, 1104, 1100, 760, 1510). This is
a genuine failure mode distinct from BM25's: the embedding model correctly
identifies "this is a door/code access-failure message" as a topic, but
can't distinguish *which specific incident/guest* the query is about when
several guests phrased near-identical short complaints — something
BM25's exact-token matching (which can key on the specific wording "code
is not working" vs. a differently-phrased incident) handled better here.
Worth flagging for the writeup as a concrete counter-example to "more
semantic is strictly better": semantic breadth can blur fine-grained
distinctions that lexical overlap preserves, when a corpus has many
near-duplicate short complaints about the same broad topic.

**multi_turn (avg recall 0.325, up from BM25's 0.100, against the same
lowered structural ceiling)** — improved, but still the weakest type
after cross_lingual and still short of even its own achievable ceiling on
two of three affected entries. q19 hits its full ceiling exactly (3/3
indexable messages retrieved, recall 0.600 = ceiling). q18 remains a
**complete miss** (recall 0.000, same as BM25) — a "summarize the
substitution issue" query still shares essentially no signal, semantic or
lexical, with the individual thread messages it should surface;
embeddings did not fix this one. q20 improved only modestly (0.2, same as
BM25) against its 0.4 ceiling. q21 (kitchen equipment) is the standout:
BM25 scored a clean 0.000 here (zero shared vocabulary between "kitchen
equipment/cooking supplies" and "pots and pans"/"baking sheet"/"aluminum
foil"/"meat thermometer"); embeddings recover **0.500** — a direct,
concrete demonstration of the category-to-instance generalization gap
embeddings can close that BM25 structurally cannot.

**cross_lingual (avg recall 1.000, up from BM25's 0.000)** — the headline
result. **Both** q22 (Spanish) and q23 (Czech) hit a clean recall of 1.0.
This confirms the model choice mattered: `paraphrase-multilingual-MiniLM-
L12-v2` is genuinely multilingual (not just "happens to share some
subword tokens with English") and bridges both languages in this corpus
without any translation step or language detection in the pipeline. This
is also the cleanest evidence that BM25's 0.000 on this type was a
**method** limitation (no cross-lingual matching mechanism at all), not
solely the tokenization-damage bug documented in the BM25 report for
Czech — embeddings have no equivalent tokenization step at all (no regex
token boundaries, no stopword list, no accent-stripping), and still
recovered q23 perfectly, confirming that bug was a compounding factor on
top of the real gap, not the primary cause.

**true_negative** — see dedicated section above; low relative to genuine
matches but with meaningfully less separation than BM25 showed, flagged
explicitly as instructed rather than glossed over.

## Scope confirmation

- `data/queries.json` and `scripts/bm25_baseline.py` were not modified.
- No RRF, hybrid combination, or reranking was implemented — per
  `STEP2_FASTFOLLOW_HYBRID.md`, deferred until after this comparison ships.
- Stopping here, alongside the comparison document, for review.
