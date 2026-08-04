# Session 0 Q2 precursor — does the "conversation-UUID problem" still exist?

Verified against a live probe (`prototypes/reservation_conversation_id_probe.py`, read-only,
GET only), not inherited from Phase 1/2 planning docs. `PHASE2_PLAN.md:57` and
`PHASE3_PLAN.md:47` both assert *"get-reservation-messages needs an internal conversation UUID
that get-reservations does not surface"* and frame the ID cache as the fix. That claim was never
checked against a live response shape — it was carried forward from the Phase 1 MCP experience.
This note checks it directly before Q2's bulk-access probe is written, the same way
`docs/phase3/session0_q3.md` re-derived the draftability table from source instead of the plan's
guess.

## Does get-reservations include a conversation-level ID?

**Yes.** The raw, unfiltered `/reservations` response includes a top-level `conversation_id`
field on every reservation record, plus two more conversation-shaped fields: `conversation_language`
and `last_message_at`. Confirmed live against 41 real reservations (2026-05-06 → 2026-10-03,
window matching `hospitable/__main__.py`'s `--smoke` mode). Sample record:

```
"id": "433fc222-775b-4bbd-998f-d3ef4afe65cd",
"conversation_id": "8f136f8c-942a-42ea-a472-74f7279ea7b6",
"conversation_language": "Russian",
"last_message_at": "2026-01-08T04:51:57+00:00",
```

`conversation_id` is a distinct UUID from the reservation's own `id` — not a duplicate or alias.
No other nested include object (`guest`, `listings`, `properties`, `financials`, `review`)
carries a conversation/thread/message-shaped key.

This directly contradicts the planning docs' "does not surface" framing. **That half of the
Phase 1/2 assumption is stale and should be corrected, not re-confirmed.**

## What is the current resolution path?

**There isn't one — and `conversation_id` isn't it.** Read directly, not inferred from names:

- `checks/unanswered_inquiry.py` never touches reservations at all — it works off inquiries and
  `hdata.get_inquiry_thread()`. It's not the bridge for this question.
- `hospitable/data.py:126-149` — `get_reservation_messages(client, reservation_uuid)` — takes the
  reservation's own `uuid` (normalized from `id` via `_uuid()`, `hospitable/data.py:35-37`) and
  passes it straight through to `client.get_messages(reservation_uuid)`. No lookup, no cache read,
  no second field extracted from the reservation record.
- `hospitable/client.py:144-163` — `get_messages()` calls `GET /reservations/{reservation_uuid}/messages`
  using that same reservation UUID directly as the path parameter. The only "resolution" is that
  `/messages` is a nested sub-resource of `/reservations/{id}` — no separate ID is looked up at all.
- `CURRENT_STATUS.md:30` records the decision explicitly: *"ID cache: **drop it** — not needed."*
  This was decided in Phase 2 Session 0 but the planning docs (`PHASE2_PLAN.md`, `PHASE3_PLAN.md`)
  were never updated to match — they still describe the cache as required/planned.
- `grep` for `conversation_id` across `hospitable/`, `checks/`, and `tests/` returns zero hits.
  The field exists in the API response and is entirely unused by Phase 2's code.

So the "resolution path" the task asked to find doesn't exist as a distinct step — reservation UUID
*is* the message-thread key, by REST nesting convention, not by any cache/lookup Phase 2 built.

## Confirmed live: does this path actually work?

**Yes**, for all 5 reservations tested (mix of `accepted` and `cancelled`, pulled via
`hdata.get_reservations()` from the same 41-reservation window above):

| reservation (short) | status | get-reservation-messages result |
|---|---|---|
| `433fc222` | cancelled | OK — 0 messages |
| `25bf9ab2` | accepted | OK — 3 messages (host only) |
| `9e49aab4` | accepted | OK — 15 messages (guest + host) |
| `2077150e` | accepted | OK — 12 messages (guest + host) |
| `fafb9d9a` | cancelled | OK — 3 messages (guest + host) |

Every call returned 200 with real message data (not just empty-thread no-ops — two reservations
returned double-digit message counts with both guest and host senders present). No 404, no 410,
no evidence `conversation_id` was required or even consulted server-side.

## Bottom line

Both halves of the inherited assumption were wrong, in opposite directions:

1. **get-reservations does surface a conversation-shaped field** (`conversation_id`) — the "does
   not surface" claim in `PHASE2_PLAN.md`/`PHASE3_PLAN.md` is stale.
2. **But no resolution step is needed anyway** — `get-reservation-messages` is addressed by the
   reservation's own UUID (a REST sub-resource, not a lookup), and `conversation_id` is unused
   dead weight for this purpose. The "ID cache to map reservation → conversation UUID" the plans
   describe was correctly dropped in `CURRENT_STATUS.md`, just never removed from the plan text
   itself.

For Q2's bulk-access probe: there is no cache to test the coverage of, and no second ID to look up
per reservation. The bulk question reduces to "does `/reservations/{uuid}/messages` behave the same
at historical-backfill volume and rate," using the reservation UUIDs `get-reservations` already
returns — not an ID-bridging problem.
