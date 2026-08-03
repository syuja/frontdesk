# Phase 3 Plan — RAG + Fix-Drafting

> Status: not started. Session 0 (derisk) is the next action.
> Phase 2 shipped: deployed nightly auditor, 5 calibrated checks, 142 tests, blog + dev.to live.
> Severity-ladder warm-up complete and deployed.

---

## What changes in Phase 3

Phase 2 was read-only. Blast radius: zero. The worst failure was a missed alert.

Phase 3 introduces **writes** — guest-facing replies and Knowledge Hub edits. A bad draft that
gets sent is a real guest reading a wrong or off-tone message about a property they're paying for.
That shift, more than the RAG, should drive the architecture.

Two rules that follow from it, and are not negotiable:

1. **Never auto-send.** Every guest-facing action passes a human approval gate. This is permanent,
   not a Phase 3 training wheel that gets removed once drafts "get good."
2. **Drafts are inert until approved.** A generated draft is data in a table, not a queued action.
   Nothing fires on a timer.

---

## Session 0 — derisk pass (no build)

Three questions to answer before writing any Phase 3 code. Mirrors the Phase 2 Session 0 pattern
that caught the conversation-UUID problem early.

### Q1 — How big is the corpus, really?

Pull the full message history via the REST API. Record:

- Number of conversations
- Total messages
- Messages per conversation (min / median / max)
- Rough token count of the whole corpus
- Date range covered

**Why it matters:** this decides whether embeddings are warranted or whether keyword retrieval is
honestly sufficient. At a few hundred messages, BM25 over the raw text may match or beat embeddings
and costs nothing to run. Do not decide the retrieval stack before this number exists.

### Q2 — Does bulk thread access work?

Phase 1 documented that `get-reservation-messages` needs an internal conversation UUID that
`get-reservations` does not surface. Phase 2 solved this for the nightly window. Confirm it holds
for a **full historical backfill**:

- Can every historical conversation be reached, or only recent ones?
- Rate limits on a bulk pull — how many requests, how long, does it need throttling/resume?
- Does the existing ID cache cover this, or does backfill need its own path?

### Q3 — Which findings are actually draftable?

Not all five. Walking them:

| Check | Draftable? | Why |
|---|---|---|
| `unanswered_inquiry` | **Yes** | A guest asked something; a reply is the fix. Highest value. |
| `actionable_review` | **Yes** | A public review response is text. Tone-sensitive. |
| `knowledge_hub_hygiene` | **Yes** | Stale/missing KH entry; the fix is written content. Not guest-facing — lower risk. |
| `smartlock_battery` | No | The fix is buying batteries. No text to draft. |
| `turnover_gap` | No | The fix is scheduling. No text to draft. |

**Output of Session 0:** a short written note answering all three, plus a recommendation on
retrieval approach backed by the Q1 number. Same discipline as Phase 2's Session 0 note.

---

## Scope — what ships in Phase 3

The roadmap draft listed six workstreams: RAG, fix-drafting, approval flow, deferred LLM checks
(guest substitution, language mismatch), per-inquiry summaries, and PriceLabs MCP integration.
That is larger than all of Phase 2, which took six weeks for five checks.

**Phase 3 ships:** RAG over message history + fix-drafting for `unanswered_inquiry` only,
behind an approval flow, with cost instrumentation.

**Fast-follow (after Phase 3 ships and is written up):**

- Extend drafting to `actionable_review` and `knowledge_hub_hygiene`
- Per-inquiry summaries ("what has this guest actually asked?")
- Deferred LLM checks: guest substitution, language mismatch

**Deferred to Phase 4 or later:**

- PriceLabs MCP integration (pricing is adjacent to conversion, not operations — see project split)

**Rationale:** one draftable finding end-to-end proves the whole pipeline — retrieval, generation,
approval, send, cost tracking. Adding the second and third finding after that is incremental and
low-risk. Starting with all three multiplies the surface area before any of it is validated.

---

## Build order

Each step ends green (tests passing) before the next begins. Same checkpoint discipline as Phase 2.

**Step 1 — Message history ingestion**
Backfill the full conversation corpus to a local store. Idempotent and resumable — a failed run
mid-backfill should not corrupt or duplicate. Incremental update path for new messages.

**Step 2 — Retrieval layer**
Build **both** BM25 and embedding-based retrieval. Evaluate on a hand-labeled set of real queries
("what did this guest ask about parking?", "how have I handled noise complaints?"). Record which
wins and by how much. See "Retrieval decision" below.

**Step 3 — Draft generation for `unanswered_inquiry`**
Given a flagged inquiry, retrieve relevant history, generate a candidate reply. Log tokens and
cost per generation from the very first call.

**Step 4 — Approval flow + draft state**
Telegram inline buttons. Draft lifecycle in DynamoDB. Approved drafts send via the existing
Hospitable send path; rejected drafts are retained for later eval work.

**Step 5 — Widen (fast-follow, only after Steps 1–4 are green)**
`actionable_review` and `knowledge_hub_hygiene` drafting.

---

## Architecture decisions

### Retrieval decision

**Chosen:** local vector store (`sqlite-vec`, or a numpy array persisted to S3), embeddings
computed at build time and refreshed on ingest — **plus** a BM25 baseline retained for comparison.

**Rejected:** managed vector DB (Pinecone, Weaviate). At this corpus size a network dependency
inside a Lambda is added latency, added failure mode, added cost, and added secret to manage, for
no measurable retrieval benefit.

**The interesting engineering move — and the blog post:** implement both, measure retrieval quality
on real queries, and publish the comparison. At this volume BM25 may well be competitive. Saying so
with numbers is a stronger signal than silently shipping a vector DB because it is the expected
answer. Right-sizing awareness reads as senior; the vector store itself does not.

If Session 0's Q1 comes back small enough that embeddings are clearly unjustified, ship BM25 and
write **that** up. The honest measurement is the artifact either way.

### Approval flow

**Chosen:** Telegram inline buttons (Approve / Reject / Edit). API Gateway + Lambda handles the
callback.

**Rejected:**
- *Reply-to-approve* — free-text parsing is ambiguous; "ok" vs "ok but change the second line"
  is a parsing problem with a guest-facing failure mode.
- *Web approval UI* — a whole new surface to build, host, and secure, for a single user.

**Why Telegram:** the digest already arrives there, so approval lands in the same context as the
alert. No new auth surface. The bot and its delivery path are already built and debugged.

### Draft state

**Store:** DynamoDB.

**Lifecycle:** `draft → approved → sent`, with `rejected` and `expired` as terminal states.
TTL on stale drafts so unapproved items age out rather than accumulating.

**Record per draft:**

- Finding ID (links back to the auditor finding that triggered it)
- Retrieved context (which messages were used — needed for Phase 5 evals and for debugging bad drafts)
- Draft text
- Model used, prompt tokens, completion tokens, computed cost
- Status + timestamps for each transition

**Why capture retrieved context:** without it, a bad draft cannot be diagnosed as a retrieval
failure vs. a generation failure. Phase 5's eval framework needs this too.

### Cost instrumentation (do not defer)

Log tokens and dollars **per operation**, starting with the first generation call. Phase 4 is the
inference routing phase, and its entire story is "here is what it cost before, here is what it
cost after, here is the measured quality tradeoff." That baseline cannot be reconstructed later.
Cheap to add now, painful to backfill.

---

## Non-goals

- No auto-send. Ever.
- No PriceLabs integration (pricing/conversion is Listing Optimizer territory).
- No competitor or market analysis — that is the Listing Optimizer, a separate project.
- No routing/model-selection work — that is Phase 4, and it needs Phase 3's traffic to be
  motivated by real numbers.
- No eval framework — that is Phase 5. (But capture the data Phase 5 will need: retrieved context,
  draft text, approve/reject decisions. Every rejected draft is a labeled negative example.)

---

## Definition of done

- Full message history ingested, refresh path working
- Retrieval implemented and **measured** (BM25 vs. embeddings, with numbers)
- `unanswered_inquiry` drafts generated with retrieved context
- Approval flow live: draft arrives in Telegram, approve sends, reject discards, state persists
- Cost logged per generation
- Tests green (Phase 2 ended at 142; expect meaningful additions)
- Deployed to AWS via SAM
- Project page updated + blog post + dev.to cross-post (canonical URL set)

**Phase 3 is not done until deployed AND written up.** Same rule as Phase 2.

---

## Blog angles to watch for while building

Lead with decisions and surprises, not architecture diagrams. Candidates:

- The BM25 vs. embeddings measurement — especially if BM25 holds up
- Why only 3 of 5 checks are draftable at all (severity ≠ actionability)
- The approval gate as a permanent design constraint rather than a temporary safety measure
- Whatever breaks in the historical backfill (Phase 2's best writing came from the bugs)

---

## How Phase 3 feeds Phases 4–5

- **Phase 4 (routing):** Phase 3 creates the inference workload — embeddings at build time,
  generations per night. Cost instrumentation from Step 3 becomes the baseline the routing work
  optimizes against.
- **Phase 5 (evals):** every approve/reject is a human label. Retrieved-context records make
  retrieval quality measurable separately from generation quality. Phase 3's data collection is
  what makes Phase 5 possible.
