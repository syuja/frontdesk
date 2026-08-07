# Session 0 Q1 — Corpus count for the retrieval decision

Exact count over the account's full history, not a sample. `PHASE3_PLAN.md`'s Q1 asks how big the
message corpus really is, because that number is meant to decide whether embeddings are worth
building or BM25 alone is "honestly sufficient." A first draft of this task proposed sampling 100
of 160 reservations and extrapolating; reviewing that draft found the population small enough
(160 total) that pulling all of it costs about the same as a partial sample, so this run fetched
message threads for **all 160** reservations — no sampling, no extrapolation.

Probe script: `prototypes/message_corpus_probe.py`. Reused Q2's already-fetched 20 oldest threads
and its 160-reservation list from `logs/session0_q2_cache.json` (no re-fetch), fetched the
remaining 140 via the same `hdata.get_reservation_messages()` path Q2 already validated. Full
results (incl. raw message bodies, for this token estimate): `logs/session0_q1_cache.json`
(gitignored, local only — same PII convention as Q2's cache).

## Counts

Over all 160 reservations in the account's full history (2025-10-03 → 2026-08-05):

| Metric | Value |
|---|---|
| Reservations | 160 (144 success, 16 empty-but-successful, 0 errors) |
| Total messages | **1,341** |
| Messages per conversation | min 0 / median 6.0 / max 40 |
| Total words (message bodies) | 88,389 |
| Estimated tokens | **~117,852** |
| Date range covered | 2025-10-03 → 2026-08-05 |

**Token estimate method:** word count ÷ 0.75 (the standard rough English words-to-tokens ratio).
`tiktoken` is not a project dependency (checked `requirements.txt`/`pyproject.toml`/`uv.lock`) and
wasn't added for a throwaway probe — this is an approximation, not an exact tokenizer count.

The 16 empty threads are all reservations with `0` messages returned as a successful `200` — kept
separate from errors, same as Q2 (there were 0 errors this run). These are mostly the same kind of
same-day-cancelled bookings Q2 already found in its 20-sample.

## Request cost of the full pull

140 new HTTP requests (the other 20 were reused from Q2's cache, not re-fetched), **45.6s wall
time, 0 429s, 0 5xx retries.** Notably different from Q2's own 20-reservation burst, which hit 2
live 429s — this run's 140-call burst hit none. Read this as **throttling being intermittent
rather than reliably triggered by burst size** (Q2: 2/20 calls throttled; Q1: 0/140), not as
evidence the account-level rate limit doesn't exist — Q2 already proved it does. A real Step 1
ingestion job should still budget for occasional 429s (the existing `Retry-After` backoff in
`HospitableClient.get()` already handles them transparently, as it did in Q2), just not assume a
fixed rate.

## Applying the plan's actual decision framework

The first draft of this task's plan cited specific numeric thresholds (`<2,000` → BM25 only,
`2,000–10,000` → build both, `>10,000` → embeddings only, median > 40 → embeddings) as
"pre-committed" — those don't exist anywhere in `PHASE3_PLAN.md` or elsewhere in the repo
(confirmed by grep before this run). What the plan actually commits to, independent of Q1's count
(`PHASE3_PLAN.md:106-139`):

> **Chosen:** local vector store... embeddings computed at build time... **plus** a BM25 baseline
> retained for comparison.
> ...implement both, measure retrieval quality on real queries, and publish the comparison.
> If Session 0's Q1 comes back small enough that embeddings are clearly unjustified, ship BM25 and
> write that up.

So Phase 3's Step 2 already commits to building **both** BM25 and embeddings and measuring them on
real queries — that's not conditional on Q1's number. The only thing Q1's count can actually decide
is narrower: does the corpus read as **"small enough that embeddings are clearly unjustified"**
(per the plan's own bar — *"at a few hundred messages, BM25... may match or beat embeddings"*), in
which case the honest call is to skip building embeddings and ship BM25 alone?

**Named decision: no — proceed with Step 2's already-committed build-both-and-measure plan.**
1,341 messages is measurably past "a few hundred" (roughly 1.5–3× it), spread across 160
conversations with a median of 6 messages and a max of 40 — not a huge corpus, but not the
obviously-tiny case the plan described as the trigger for skipping embeddings outright. This
doesn't clear the bar for "clearly unjustified." Build both, measure both on real queries as Step
2 already specifies, and let the measurement — not Q1's count alone — decide which one Phase 3
actually ships with.

## Data for reuse

`logs/session0_q1_cache.json` (gitignored, not committed — contains raw message bodies) has all
160 reservations' message threads (20 reused from Q2's cache, 140 newly fetched), the stats above,
and this run's request/timing/throttle metrics. Step 2 (retrieval layer) can build its BM25 index
directly from this file's `messages_raw` fields without re-pulling from the API, though a real
Step 1 ingestion run will supersede it as the source of truth once built.
