# Session 0 — consolidated summary

`PHASE3_PLAN.md`'s Session 0 asks for one deliverable: *"a short written note answering all three
[questions], plus a recommendation on retrieval approach backed by the Q1 number."* That note was
answered across three separate docs as each question was investigated
(`docs/phase3/session0_q1.md`, `_q2.md`, `_q3.md`). This is that single note — the last artifact
before Step 1 (ingestion) starts. Each section links back to its detailed doc for full citations
and file:line evidence; nothing here is re-derived, except the corpus-scope correction below.

## 1. Corpus scope

**160 reservations** (2025-10-03 → 2026-08-05, the account's actual full history) **+ 36
inquiries** — **~1,454 messages combined.**

One correction beyond what `_q1.md`/`_q2.md` say on their own: their corpus count (1,341 messages)
covers only **reservation** message threads (`hdata.get_reservation_messages`). Phase 3's actual
drafting target — `unanswered_inquiry`, the one check Phase 3 ships drafting for — pulls its
context from **inquiry** threads instead (`hdata.get_inquiry_thread`, a different endpoint), which
Q3's live probe separately found to be 36 inquiries / 113 messages. **Step 1 must backfill both
sources**, not just reservations — reservation messages alone would miss the exact data
`unanswered_inquiry` drafting needs.

Full detail: `session0_q1.md`, `session0_q2.md`, `session0_q3.md` (`unanswered_inquiry` row).

## 2. Q1 counts

| Metric | Value |
|---|---|
| Reservation messages | 1,341 (160 reservations, 144 success / 16 empty / 0 error) |
| Inquiry messages | 113 (36 inquiries, live-verified) |
| **Combined** | **~1,454** |
| Messages per reservation-conversation | min 0 / median 6.0 / max 40 |
| Estimated tokens (reservations only) | ~118K (word-count ÷ 0.75 — approximate; `tiktoken` isn't a project dependency) |
| Date range | 2025-10-03 → 2026-08-05 (full account history) |

Exact counts, not extrapolated — the population was small enough to pull in full rather than
sample. Full detail: `session0_q1.md`.

## 3. Q2 probe classification

| Finding | Classification | Result |
|---|---|---|
| UUID unresolvable for any sampled thread | Hard blocker | **Not triggered.** 0 failures across all 160 reservations tested (Q2's 20 + Q1's follow-on 140). No separate conversation-UUID resolution exists or is needed — the REST API takes the reservation's own UUID directly. |
| Older threads unreachable past a retention horizon | Documented gap | **Not testable yet.** Account history is only ~10 months deep; the oldest reservation was fully reachable, but there's no long-tail data old enough to actually exercise this. Re-test once the account has >1 year of history. |
| Rate limits require throttling | Cost, not blocker | **Real, but intermittent.** 2/20 calls throttled in Q2's burst, 0/140 in Q1's larger follow-on run — not reliably proportional to burst size. Existing `Retry-After` backoff in `HospitableClient.get()` already handles it; budget for it, no new code needed. |
| Pagination loops, truncates, or drops records | Hard blocker | **Not triggered — no pagination mechanism exists on this endpoint.** Confirmed empirically: `page`/`per_page` params are silently ignored on `/reservations/{uuid}/messages`; the full thread always returns in one response. |
| Bulk pull works but is slow | Cost, not blocker | **Not a real cost at this account's scale.** ~6.5s (Q2) + ~46s (Q1's 140-call follow-on, mostly real request time, not throttling) for the full historical pull. |

Full detail: `session0_q2.md` (plus its addendum correcting the throttle-scaling prediction).

## 4. Named retrieval decision

**Proceed with `PHASE3_PLAN.md`'s already-committed Step 2: build both BM25 and embeddings, measure
on real queries, publish the comparison.** Not "BM25 only."

The plan's own bar for skipping embeddings is a corpus "small enough that embeddings are clearly
unjustified" (*"at a few hundred messages, BM25... may match or beat embeddings"*). At ~1,454
messages combined, this doesn't clear that bar — it's meaningfully past "a few hundred." A
first-draft version of the Q1 task cited specific numeric thresholds (`<2,000` → BM25 only, etc.)
as "pre-committed" in `PHASE3_PLAN.md`; those don't exist anywhere in the repo. The plan's actual,
already-committed decision doesn't change based on Q1's number — it was always "build both and
measure." Q1's number only tests whether that could be skipped; it can't be here.

Full detail: `session0_q1.md`.

## 5. Q3 draftable list

| Check | Draftable? | Note |
|---|---|---|
| `unanswered_inquiry` | **Yes** | Highest value. `Finding.detail` carries no message text — must re-fetch via `hdata.get_inquiry_thread(entity_id)` at draft time. Live-confirmed field: `body`, plain text. |
| `actionable_review` | **Yes** | Not what the plan described: it's the **host's own review of the guest** (confirmed via the OpenAPI `GuestReview` schema), not a response to a guest-authored review. Still textual. Re-fetch via `hdata.get_guest_reviews()` — Hospitable already supplies `public_message` (draft) and `ai_suggestions` (AI text), both currently unread by the check. |
| `knowledge_hub_hygiene` | **Yes** | `Finding.detail` has only a 60-char truncated, normalized snippet, and no item-level `entity_id` (both variants use `property_uuid`). Re-fetch via `hdata.get_knowledge_hub(property_uuid)` and re-match by normalized content. |
| `smartlock_battery` | No | Pure device telemetry. No guest-facing text. |
| `turnover_gap` | No | Pure scheduling data. No guest-facing text. |

The plan's Yes/No split was directionally right; its assumption that the "yes" Findings already
carry draftable text was wrong in all three cases — Step 3 must re-fetch via `entity_id`/
`property_uuid`, never read drafting input off `Finding.detail`. Full detail: `session0_q3.md`.

## Carried forward into Step 1's design

- Backfill **both** inquiry and reservation message sources (§1) — not reservations alone.
- Budget retry/backoff for intermittent 429s beyond the existing per-UUID throttle — already
  handled by `HospitableClient.get()`, just don't assume a fixed, predictable rate (§3).
- Retention-horizon question stays open, non-blocking — re-test once the account has more history
  (§3).
- Draft generation re-fetches source entities via `entity_id`/`property_uuid` at draft time
  (`get_inquiry_thread`, `get_guest_reviews`, `get_knowledge_hub`) — never reads `Finding.detail`
  as drafting input (§5).

**Session 0's three questions are answered. The open items above are known and non-blocking. Step
1 can start.**
