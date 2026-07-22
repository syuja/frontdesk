# Project 1 (Hospitable Agent) — Phase 2 Plan: Read-Only Scheduled Auditor

> Starting point for review. Weeks 4–6. Read-only, no writes, no guest messages.
> Goal: automate the detection half of the loop run by hand in Phase 1.

---

## Artifact: BOTH (project page + blog post)

This phase ships a blog post, not just a project page. Here's the reasoning so it's a deliberate choice, not a default:

- **The bracket got only a project page** because it was a clean build with no hard-won lesson.
- **The auditor has a real engineering story:** working around documented MCP/API gaps (ID caching, opaque errors, the conversation-UUID problem), the deterministic-vs-LLM check split, and the move from raw data to decisions (the project thesis). That's 1,000+ words of genuine content.
- The blog post angle writes itself: *"I automated the audit that found real problems in my Airbnb — here's what it catches and what I had to work around."*

So: project page (short description, like Futbol Report) **plus** a blog post (the lesson). Cross-post to dev.to with canonical URL set. That's the full definition of done.

---

## What this phase is NOT

Locking scope before it drifts:

- **No writes.** It never modifies Hospitable data, never messages guests, never edits rules or the Knowledge Hub.
- **No fix-drafting.** Drafting responses is Phase 3 (it needs RAG context anyway).
- **No PriceLabs, no pricing analysis, no market data.** Parked, stays parked.
- **Not a rebuild of the MCP.** Scheduled automation calls the REST API directly; the MCP stays for interactive Claude use.
- **No new infrastructure patterns.** Reuse the Futbol Report stack wholesale.

---

## Architecture (reuses Futbol Report stack)

```
[EventBridge Scheduler: nightly cron, America/New_York]
        ↓ triggers
[Lambda: Python 3.12, x86_64]
        ↓ 1. read from ID cache (DynamoDB or S3-JSON)
        ↓ 2. pull fresh data from Hospitable REST API (PAT auth)
        ↓ 3. run checks (deterministic + LLM where needed)
        ↓ 4. update ID cache with any new entities
        ↓ 5. compile severity-tagged digest
        ↓
[Telegram: digest to "Jean Claude Van Bot," chat 8795167083]
```

Same Lambda + EventBridge + Telegram plumbing already debugged in Project 0. The new parts are: the Hospitable REST client, the ID cache, and the check logic.

---

## Session 0 (derisking): Personal access token + API notebook

**Do this first, before any Lambda work.** One short session. Answers the open questions that determine the rest of the build.

1. Generate a Hospitable personal access token (PAT).
2. In a notebook, hit the REST API: pull reservations, messages, inquiries, reviews, Knowledge Hub, calendar.
3. **Answer the conversation-UUID question:** does the REST API surface the internal conversation UUID that `get-reservation-messages` needs, or is it still missing the way it was in the MCP? This single answer decides how much the ID cache has to do.
4. Confirm per-child-listing calls work (parent UUID doesn't aggregate — verified in Phase 1; reconfirm via REST).
5. Note rate-limit behavior and any auth quirks (Phase 1 saw opaque "No approval received" errors on the MCP — check whether the REST API is cleaner).

**Output:** a notebook + a short note answering: what data is cleanly available, what needs the ID cache, what's still painful. That note shapes the check list below.

---

## The check catalogue (review and prioritize this)

These come straight from Phase 1's four finding categories. **This is the part to review and decide on** — which checks are worth building first, which are nice-to-have, which are skippable. Each is tagged deterministic (no LLM) or LLM (needs a model pass).

### Category 1 — Liability

- **Guest substitution signal** [LLM] — message text suggests the registered guest isn't the one staying (AirCover exposure). The actual case that triggered this in Phase 1. Hard to detect without reading text.
- **Unverified-guest indicators** [LLM] — references to additional/unregistered occupants.

### Category 2 — Revenue / conversion

- **Unanswered inquiry past threshold** [deterministic] — inquiry with no host reply after N hours. The 3-hour gap that lost a whole-home booking in Phase 1.
- **Language mismatch** [LLM or cheap heuristic] — inquiry in one language, reply in another (the Spanish-inquiry-answered-in-English case).
- **Stalled booking conversation** [deterministic] — thread went quiet mid-negotiation.

### Category 3 — Quietly broken

- **Review window near expiry** [deterministic] — review-able reservation within 3 days of the window closing. Pure date math. The problem-guest review almost lost in Phase 1.
- **Stale Knowledge Hub entries** [deterministic + checklist] — door codes / parking / check-in info that's outdated or missing vs. a maintained checklist. Seven stale entries found in Phase 1.
- **Post-checkout / canned message typos** [deterministic, one-time + on-change] — the months-old typo case.

### Category 4 — Unused-feature / hygiene

- **Empty or thin Knowledge Hub sections** [deterministic] — sections that should be filled but aren't (parking was absent — the #1 guest question).
- **Tasks not set up for upcoming turnovers** [deterministic] — turnover with no cleaning task attached.

### Severity tagging

Each fired check gets a severity so the digest is scannable:

- **CRITICAL** — liability (guest substitution), review window expiring within 24h
- **HIGH** — unanswered inquiry losing revenue, review window 2–3 days out
- **MEDIUM** — stale Knowledge Hub entry, language mismatch
- **LOW** — hygiene (empty sections, missing tasks)

---

## Build sequence (weeks 4–6)

**Week 4 — Foundation**
- Session 0 (PAT + notebook + conversation-UUID answer)
- REST API client module (auth, the core GET calls, rate-limit handling)
- ID cache (DynamoDB or S3-JSON; map reservation → conversation UUID, parent → child listings)
- Pick 2–3 deterministic checks to start (review-window expiry, unanswered inquiry, stale KH entry — highest proven value, no LLM needed)

**Week 5 — Checks + delivery**
- Implement the chosen deterministic checks end to end
- Severity tagging + digest formatter
- Telegram delivery (reuse Project 0 bot)
- Run locally against the real account; tune thresholds

**Week 6 — Schedule + harden + ship**
- Package Lambda zip (reuse the manylinux2014 wheel process from Project 0)
- Deploy: Lambda + EventBridge nightly schedule + env vars (PAT, Telegram token, FOOTBALL-style key handling)
- Add 1–2 LLM checks if time allows (guest substitution is the highest-value LLM check)
- Run unattended for a few nights, confirm digests arrive and catches are real
- Project page + blog post + dev.to cross-post

---

## Definition of done

- Auditor runs nightly unattended for one week
- Severity-tagged digest arrives on Telegram each run
- At least one real catch during that week (or a confirmed clean week with checks verified manually)
- Public GitHub repo with clean README
- Project page on samiryuja.dev
- Blog post live + cross-posted to dev.to (canonical URL set)

---

## How this feeds Phases 3–5

So the scope discipline pays off later:

- **Phase 3 (RAG + fix-drafting):** the auditor's LLM checks get better with retrieval context; fix-drafting lands here because drafts need message history.
- **Phase 4 (inference routing):** the nightly LLM checks are the real cost driver — cheap-model screening with Claude escalation on flagged items. Per-check cost tracking.
- **Phase 5 (evals):** Phase 1's hand-audit findings are the golden dataset. "Does the auditor catch what I caught by hand?" = precision/recall with real ground truth.

---

## Open decisions for review

Before building, decide:

1. **Which checks ship in v1?** Recommendation: start with the three highest-value deterministic ones (review-window expiry, unanswered inquiry, stale KH entry). They need no LLM, derisk fastest, and all three caught real problems in Phase 1.
2. **ID cache store:** DynamoDB (cleaner, more "real") vs S3-JSON (simpler, fine at this scale). Decide after Session 0 reveals how much caching is actually needed.
3. **Schedule frequency:** nightly is the default. Could be twice-daily if inquiry-response-time checks need to fire faster. Decide based on how time-sensitive the revenue checks are.
4. **LLM checks in v1 or v2?** Guest substitution is high-value but LLM-dependent. Could ship deterministic-only first, add LLM checks in week 6 or defer to a fast-follow.
