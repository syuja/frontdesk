# Session 0 Q3 — Which findings are actually draftable?

Verified against source, not the plan's assumption. `PHASE3_PLAN.md`'s Session 0 Q3 table guessed
`unanswered_inquiry`, `actionable_review`, and `knowledge_hub_hygiene` are draftable and
`smartlock_battery`/`turnover_gap` are not. This note re-derives that answer from the actual
`Finding`-construction code in `checks/*.py`, the shared `Finding` dataclass
(`checks/finding.py`), the data-pull layer (`hospitable/data.py`), and the Hospitable OpenAPI
schema (`openapi_hospitable_api_documentation.json`).

## Finding schema

`Finding` (`checks/finding.py:29-42`) has exactly 9 fields: `check`, `severity`, `property_uuid`,
`property_name`, `title`, `detail`, `entity_id`, `guest_name`, `extra`. There is no `message`,
`review_text`, or `content` field. `detail`'s own docstring states its purpose: *"specifics: ids,
dates, ages; UUIDs for tracing (verbose only)"* — designed as a structured trace signal for Phase
2's read-only digest, not as drafting input.

## The table

| Check | Draftable? | Why | Fields available to a drafter |
|---|---|---|---|
| `unanswered_inquiry` | **Yes** | A guest asked something; a reply is the fix. Highest value. | `detail` is structured-only: `inquiry=<uuid8> last_message_at=<ts> gap=<Nh>` (`checks/unanswered_inquiry.py:158-162`). No guest message text on the Finding — the check fetches the full thread via `hdata.get_inquiry_thread()` but only reads `sender`/`created_at` off it (lines 115-121), discarding the body. A drafter must re-fetch via `hdata.get_inquiry_thread(client, entity_id)`. That helper spreads the raw API message dict unmodified (`hospitable/data.py:200-203`), so the real message text is present in the response — the field name (`body`/`text`/or `content`) isn't confirmable from the OpenAPI spec alone (the `Message` schema is an unresolved external `$ref`), but `prototypes/inquiry_sender_probe.py:181` — a diagnostic written during a real `unanswered_inquiry` bug investigation — already reads `msg.get("body") or msg.get("text") or msg.get("content")` per message, confirming engineers on this project have already located and used this data. |
| `actionable_review` | **Yes** | Not what the plan assumed — see discrepancy note below. It's the host's own review of the guest, not a response to a guest-authored review. Still textual, still the fix. | `detail` is structured-only: `review=<uuid8> checkout=<date> (<Nd> ago) | expires=<date> (<Nd> remaining)` (`checks/actionable_review.py:125-141`). The check is an explicit *"pure function... does no I/O"* that only reads `status`, `can_be_sent_now`, checkout dates, `expires_at`, `guest.first_name` — no review text field anywhere in the module. A drafter must re-fetch via `hdata.get_guest_reviews(client)`, which spreads the raw `GuestReview` object unmodified (`hospitable/data.py:228-233`). The OpenAPI `GuestReview` schema confirms the `/guest-reviews` list response already includes `public_message` (the draft/submitted review text) and `ai_suggestions` (Hospitable-generated suggested text per star rating) — both present in `audit.reviews` today, both unread by the check. |
| `knowledge_hub_hygiene` | **Yes** | Fix is written KH content, not guest-facing — lower risk. | Partial capture, the only one of the five with any real content on the Finding: `detail` embeds a normalized (lowercased, whitespace-collapsed) snippet of `item.content`, **truncated to 60 characters** (`checks/knowledge_hub_hygiene.py:104-120`). Both KH Finding variants set `entity_id=prop_uuid` (lines 87, 119) — there is no item-level id, so a drafter can't address the specific duplicate item from the Finding alone; it must re-fetch via `hdata.get_knowledge_hub(client, property_uuid)` (which returns the full untruncated `content` per item, `hospitable/data.py:237-253`) and re-match by normalized content to find the item(s) to edit. |
| `smartlock_battery` | No | The fix is buying batteries. No text to draft. | `detail` is pure device telemetry — battery %, threshold, online/offline, tripwire strings (`checks/smartlock_battery.py:166-195`). No guest-facing text anywhere in this check; none expected — it's a maintenance alert, not a messaging check. |
| `turnover_gap` | No | The fix is scheduling. No text to draft. | `detail` is pure scheduling data — `OUT: <prop> <date> (<guest>) → IN: <prop> <date> (<guest>) | gap=<N>d` (`checks/turnover_gap.py:150-163`). Guest first names are present for identification only, never message/review content. This is a pure function, no I/O — an operational/cleaning-scheduling alert, not messaging-shaped. |

## Discrepancy from the plan's original table

Two distinct corrections, both load-bearing for how Phase 3 Step 3 (draft generation) gets built:

**1. All three "yes" Findings are thinner than the plan implied.** The plan's phrasing —
*"a public review response is text,"* *"fix is written KH content"* — reads as if the review/KH
text is already sitting on the Finding, ready to hand to an LLM. It isn't, in any of the three.
`unanswered_inquiry` fetches the entire message thread and then discards the body. `actionable_review`
never touches review text at all — it's a pure function over dates and booleans. `knowledge_hub_hygiene`
captures only a lowercased, truncated-to-60-char snippet with no item-level id. This isn't a
structural guess: it's verified against `hospitable/data.py`, where all three re-fetch helpers
(`get_inquiry_thread`, `get_guest_reviews`, `get_knowledge_hub`) spread the raw API response with no
field-stripping — meaning the full text these checks need *is* reachable through the existing data
layer, just not through the Finding. **Step 3 must re-fetch the source entity by `entity_id`/
`property_uuid` at draft time; it cannot be built to read `Finding.detail` as its input.**

**2. `actionable_review` is not what the plan described.** The OpenAPI `GuestReview` schema
(`components/schemas/GuestReview`) describes it plainly: *"A host's review of a guest... regardless
of whether it is still pending, has already been submitted, or has been cancelled."* This check
tracks the **host's outgoing review of the guest** — the reciprocal Airbnb/VRBO review — not a
response to a guest-authored review of the listing, which is what "a public review response is
text" suggests. The fix is still textual either way, so the Yes verdict is unchanged, but what gets
drafted is different: the host's review of the guest. This actually strengthens the case for
drafting it first among the fast-follow checks — Hospitable already supplies a starting point via
`public_message` (locally saved draft/buffer) and `ai_suggestions` (AI-generated text per star
rating), both present in `/guest-reviews` responses today and both currently unread by the check.

## Bottom line

The plan's Yes/No split holds: 3 of 5 checks map to a textual fix, 2 don't, for the reasons the
plan gave for `smartlock_battery` and `turnover_gap`. But none of the 3 "yes" Findings carry
drafting-ready text today — Step 3 needs a re-fetch step per check, using data-layer helpers that
already exist and already return the needed fields unfiltered. And `actionable_review`'s actual
mechanism (host reviews guest, with Hospitable-supplied draft text already available) is different
enough from the plan's description that anyone designing that check's draft flow should read the
`GuestReview` schema directly rather than working from the plan's one-line gloss.
