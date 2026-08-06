# Session 0 Q2 — Does bulk thread access work?

Verified live against the real account (2026-08-06), not assumed from Phase 2's small-scale
result. `PHASE3_PLAN.md`'s Q2 asks whether Phase 2's conversation-UUID resolution (used only for
the nightly digest window — a handful of recent reservations) holds for a full historical
backfill. Probe script: `prototypes/reservation_bulk_backfill_probe.py`. Raw cached output (incl.
full message bodies, for Q1 reuse): `logs/session0_q2_cache.json` (gitignored, local only — not
committed, see PII note below).

## Resolution method reused, and where it lives

There is no separate "conversation UUID" resolution to test — Phase 2's own Session 0 already
answered this question, and the answer was to drop it:

- `CURRENT_STATUS.md:30` — *"ID cache: **drop it** — not needed."*
- `hospitable/client.py:144` `HospitableClient.get_messages(reservation_uuid)` → `GET
  /reservations/{reservation_uuid}/messages`, throttled to 2 req/min per reservation UUID.
- `hospitable/data.py:126` `get_reservation_messages(client, reservation_uuid)` wraps it,
  normalizing `sender`. Takes the reservation's own `uuid` (from `get_reservations()`) directly —
  no lookup step.
- `prototypes/reservation_conversation_id_probe.py` (pre-existing, small-scale: 5 recent
  reservations) already confirmed no conversation-shaped key exists anywhere on a raw reservation
  record, nested or flat.

This probe reuses that exact path unmodified (`hdata.get_reservation_messages`) and extends the
same confirmation from 5 recent reservations to the **20 oldest reservations in the account's
entire history**.

## Sampling

Pulled the full reservation history via `hdata.get_reservations()` (reused as-is) with a
deliberately wide window (`2010-01-01` → today) to find the account's true floor, rather than
assume one:

- **160 total reservations** in that window.
- **Actual discovered range: 2025-10-03 → 2026-08-05** (~10 months — the account's real history is
  this short; the wide 2010 start returned nothing earlier because nothing earlier exists).
- Sampled the **oldest 20** by check-in date, all statuses included (not just `accepted`):
  `2025-10-03` → `2025-10-21`.

## Per-reservation results (oldest → newest)

| # | Reservation | Check-in | Status | Result | Messages |
|---|---|---|---|---|---|
| 0 | `26fb580a` | 2025-10-03 | accepted | success | 15 |
| 1 | `1fa28c7b` | 2025-10-04 | accepted | success | 12 |
| 2 | `eeb0a484` | 2025-10-06 | cancelled | success | 18 |
| 3 | `1732ac7c` | 2025-10-08 | accepted | success | 6 |
| 4 | `50b05cab` | 2025-10-08 | accepted | success | 3 |
| 5 | `67bac9d2` | 2025-10-10 | accepted | success | 7 |
| 6 | `112092ab` | 2025-10-11 | accepted | success | 10 |
| 7 | `a665566e` | 2025-10-13 | not accepted | success | 7 |
| 8 | `e693fe1e` | 2025-10-15 | accepted | success | 8 |
| 9 | `c74f4df3` | 2025-10-15 | not accepted | success | 4 |
| 10 | `a2638451` | 2025-10-17 | not accepted | success | 2 |
| 11 | `bce4f78d` | 2025-10-17 | accepted | success | 10 |
| 12 | `d2662f28` | 2025-10-18 | not accepted | success | 2 |
| 13 | `a484f17f` | 2025-10-19 | cancelled | success | 4 |
| 14 | `9162b6c9` | 2025-10-21 | cancelled | **empty** | 0 |
| 15 | `8fed2502` | 2025-10-21 | cancelled | success | 1 |
| 16 | `e86575cf` | 2025-10-21 | cancelled | **empty** | 0 |
| 17 | `011c430e` | 2025-10-21 | cancelled | **empty** | 0 |
| 18 | `b536e60d` | 2025-10-21 | cancelled | **empty** | 0 |
| 19 | `ffc901d8` | 2025-10-21 | cancelled | **empty** | 0 |

**0 errors, 15 success, 5 empty-but-successful** (kept separate per the task spec — these are not
failures). All 5 empties are `cancelled` reservations clustered on the same check-in date
(2025-10-21), consistent with same-day booking requests that were declined/cancelled before any
message was exchanged, not a data-access problem — `get_reservation_messages` returned `200` with
`data: []` in each case, not a 404/error.

## Pagination behavior

`GET /reservations/{uuid}/messages` does **not paginate at all**, confirmed empirically (not just
from the OpenAPI spec, which was already known to be incomplete after the Q3 `Message`-schema
`$ref` finding). Tested against the sample's longest thread (`eeb0a484`, 18 messages):

- `page=1&per_page=1` → all 18 items returned (not 1).
- `page=2&per_page=1` → the exact same 18 items, identical message IDs in the same order — not a
  second page, not an error, not an empty tail.
- No `meta` block in the response at all (unlike `/inquiries` and `/guest-reviews`, which do
  paginate via `meta.last_page`).

So there's no cursor/offset to loop, truncate, or drop records — the endpoint always returns the
complete thread in a single response, and `page`/`per_page` are silently ignored. This is a
different finding than "pagination works correctly": there's no pagination mechanism to verify.
The task's ask ("confirm the cursor actually terminates") is trivially satisfied because there is
no cursor — but it also means a single long-running thread's entire body always comes back in one
response with no way to fetch it incrementally. Not a problem at the thread lengths seen here
(max 18 messages), but worth remembering if any thread ever grows very large.

## Request count, wall time, throttling

- **29 total HTTP requests** (properties + 4 pages of the 160-reservation list pull + 20 thread
  fetches + 2 pagination-test calls + 2 automatic 429 retries).
- **69.5s wall time for the full probe run** — but this includes a deliberate 31s sleep inserted
  before the pagination test to respect the documented 2-req/min-per-reservation throttle. The
  actual bulk-pull work (properties + full 160-reservation history + all 20 message threads) took
  **~6.5s** of measured fetch time, summed from per-call timings.
- **2 real 429 responses encountered**, despite the per-UUID self-throttle already built into
  `HospitableClient.get_messages()` — meaning there's a rate limit in play beyond the documented
  per-reservation one, likely a broader per-account/IP limit that a burst of ~20 distinct-UUID
  calls in quick succession can still trip. Both were handled transparently by
  `HospitableClient.get()`'s existing `Retry-After` backoff — final result was 0 errors, but a real
  backfill of the full historical corpus (all 160 reservations, not just 20) should expect to hit
  this more than twice and budget for it.
- 0 5xx retries.

## Classification against the table

| Finding | Classification | Observed? |
|---|---|---|
| UUID unresolvable for ANY sampled thread | Hard blocker | **Not triggered.** 0/20 failures, including the account's single oldest reservation. Directly confirms the plan's original worry doesn't apply — the REST API never needed a separate conversation UUID in the first place (see resolution section above). |
| Older threads unreachable past a retention horizon | Documented gap | **Not triggered — and not fully testable.** The account's real history is only ~10 months deep (2025-10-03 onward); the "oldest 20" sample already reached that actual floor, and every one of those was reachable. There is no evidence of a retention wall, but there's also no long-tail data old enough to have stress-tested one against. This should be re-run once the account has more than ~1 year of history to actually exercise this row. |
| Rate limits require throttling | Cost, not blocker | **Triggered.** 2 real 429s during a burst of 20 distinct-UUID calls, on top of the per-reservation throttle already in place. Confirms a bulk backfill needs its own throttling/backoff budget beyond the existing per-UUID 31s gap — Step 1 (ingestion) should plan for this explicitly. |
| Pagination loops, truncates, or drops records | Hard blocker | **Not triggered — no pagination exists on this endpoint to loop/truncate/drop.** Confirmed empirically: `page`/`per_page` params are silently ignored, full thread always returned in one response, no `meta` block. |
| Bulk pull works but is slow | Cost, not blocker | **Not really triggered at this account's scale.** ~6.5s of actual fetch time for 160 reservations' metadata + 20 full message threads. Slow only in the sense that a full 160-reservation backfill (not just the 20 sampled) will multiply the 429-retry cost above proportionally — that's the real cost driver, not raw latency. |

Per your framing going in: the "UUID unresolvable" row is confirmed not just unlikely but **not
triggered at all**, at the account's true historical floor. The two rows worth carrying into Step
1's design are the **throttle/cost finding** (real, concrete, and actionable — budget for 429s
beyond the documented per-UUID limit) and the **retention-horizon row's actual status**, which
isn't "documented gap, here's the horizon" as the table anticipated, but "gap not present within
available data, so the question stays genuinely open until the account has more history."

## Cached data for Q1

`logs/session0_q2_cache.json` (130KB, gitignored — contains full raw message bodies, so it isn't
committed) has:

- All 160 reservations in the full window (uuid, check-in, status).
- The 20 sampled threads' full raw message arrays (for Q1's token/corpus counting — no need to
  re-pull from the API).
- Full instrumentation: per-request timestamps, per-reservation fetch timings, the pagination
  test's raw responses, and the summary metrics reported above.

This file is local-only (path above) and lives outside git per repo convention (`.gitignore`
excludes `logs/` — consistent with how the existing prototype probes already avoid committing raw
guest data).
