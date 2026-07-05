# hospitable-concierge

Read-only nightly auditor for a short-term rental portfolio — detects actionable problems and delivers a severity-tagged digest via Telegram.

**Architecture:** EventBridge → Lambda → Hospitable REST API → Telegram digest

---

## Read-only by discipline

This auditor never writes. It never modifies Hospitable data, messages guests, edits rules, or touches the Knowledge Hub — even though the PAT has write scope. Every code path is GET-only. Fix-drafting is Phase 3.

---

## v1 Checks (all deterministic)

- [ ] **Unanswered inquiry** — conversation where the last message has `sender_type = guest` (no host reply)
- [ ] **Actionable review** — review where `can_be_sent_now` is true and checkout date math confirms the window is open
- [ ] **Stale / duplicate Knowledge Hub entry** — entry where `updated_at` age exceeds threshold, or topic appears more than once across the hub
- [ ] **Missing turnover task** — reservation window with no task returned from `/tasks`

---

## Setup

TODO

## Deploy

TODO

## Environment variables

TODO
