# Phase 1 Notes — Daily MCP Use

> Requirements discovery on myself. One entry per real interaction with the Hospitable MCP.
> The "Missing" / "Would-route" / "Eval signal" lines are doing real work — they seed Phases 2, 4, 5.
> Be specific. "Clunky" is useless; "had to re-specify the date range three times" is evidence.

---

## 2026-06-03 — Knowledge Hub + review analysis (Phase 2 decision test)

- Prompt: "get-property-knowledge-hub, full dump" + "get-property-reviews, all reviews w/ rating, date, platform"
- Tools used: get-property-knowledge-hub, get-property-reviews
- Time-saving: KH returned clean/structured; reviews surfaced ranked complaint clusters w/ platform segmentation + fixes
- Top finding (real action): Room locks = #1 complaint, 6 guests, both rooms, both platforms.
  Fix = exterior deadbolt per bedroom door. Plausibly moves multiple 4.5★→5★. HARDWARE fix, ~$40. (ops, do this)
- Memory corrected: "shared-home surprise" is NOT mostly Booking.com (3 of 4 were Airbnb). It's a
  cross-platform listing-clarity issue, not a platform bug. (Phase 1 working as intended)
- Phase 2 VERDICT: official KH tools good enough → original Phase 2 (build KH server) DEAD.
  New Phase 2 candidate: review→issue→KH-update→listing-fix loop. Decide week 4.
- Missing: nothing closes the loop reviews→KH→listing. Manual today. (→ Phase 2 candidate / Phase 5)
- Eval signal: n/a (analysis)

## 2026-06-03 — Testing message analysis for revenue/risk signals

- Prompt: "[your Pratyush revenue-loss prompt]"
- Tools used: get-reservation-messages
- Time-saving: model surfaced 3 signals from raw thread unprompted — guest substitution,
  unoffered late checkout, unmonetized parking
- Missing: this only works because *I* ran a one-off prompt on one reservation. No tool does
  this across all guests, persistently, or flags it proactively. (→ Phase 5: this is the feature)
- Eval signal: n/a (analysis, not a guest-facing draft)
- Key reframe: biggest issue is LIABILITY not lost fee — unregistered occupant (Aayushi,
  no profile) = AirCover/third-party-booking exposure. Revenue detection should weight risk, not just $.

## 2026-06-03 — get-upsells: catalog audit

- Prompt: "Run get-upsells — show me my current upsell catalog"
- Tools used: get-upsells
- Time-saving: confirmed catalog state in one call
- Finding: 3 products — Trip Insurance (active, $0 partner-managed = expected), Early Check-in
  - Late Checkout both INACTIVE but fully configured ($15–60, 4 tiers)
- Action (ops, this week): decide whether to activate Early/Late — BUT both "block the day,"
  so activating hurts tight Jun 11 / Jun 15 turnovers. Be selective.
- Missing: nothing detects upsell *opportunity* in a live conversation (catalog exists; matching doesn't). (→ Phase 5)

## 2026-06-02 — 14-day turnover outlook

- Prompt: "List my reservations for the next 14 days. and flag any days with guests leaving as a turnover day."
- Tools used: get-reservations, get-property-calendar
- Time-saving: surfaced 3 turnover days (Jun 4, 11, 15) live, no manual calendar cross-referencing
- Clunky:
- Missing: no arrival-day awareness — I need to know who checks in each day so I keep my phone on me; day-one is when problems hit. No tool weights arrival-day messages as higher-risk. (→ Phase 5 severity seed)
- Would-route-to-cheap-model:
- Eval signal: n/a (read-only)

## 2026-06-02 — Revenue-leakage signal in messages

- Prompt: n/a — observation from Pratyush (Queen, checked in today)
- Missing: nothing surfaces revenue leakage. Guest said he didn't know how to add his wife →
  unregistered extra guest → lost extra-guest fee. No tool flags "message indicates uncollected
  fee / upsell opportunity." (→ Phase 5: extend pattern detection beyond complaints to revenue signals)
- Note: pairs with get-upsells / get-purchased-upsells (MCP has these) — upsell *catalog* exists,
  but nothing detects the *opportunity* in a conversation.

## 2026-06-02 — MCP capability gap: complaints

- Prompt: n/a — capability check
- Tools used: none (reviewed available tool list)
- Missing: MCP exposes raw material — get-reservation-messages, get-property-reviews,
  get-guest-reviews — but NO tool that categorizes, clusters, or flags complaints. No
  sentiment, no topic, no severity, no platform segmentation. The analysis layer doesn't
  exist; the raw data to build it does. (→ Phase 5 confirmed: real gap, not hoped-for)

## Template (copy this for each entry)

```
## YYYY-MM-DD — <what I was trying to do>
- Prompt: "<the exact prompt I typed>"
- Tools used:
- Time-saving (what genuinely helped):
- Clunky (worked, but friction — note it precisely):
- Missing (wanted X, no tool / tool insufficient):
- Would-route-to-cheap-model (drafts that don't need Claude quality):
- Eval signal (did I edit the draft? how much?):
```

---

<!-- Fill the blanks above as you actually run the queries. Don't pre-write them. -->
