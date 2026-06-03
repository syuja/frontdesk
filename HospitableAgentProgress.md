# Hospitable Agent — Progress Tracker (Project 1)

> Last updated: June 2, 2026
> Maintained as a working doc — update at the end of each session.
> Scope: Project 1 only. The Project 0 (Futbol Report) tracker stays as its own record.

---

## TL;DR — read this first if you're picking up fresh

**Project 1 is the Hospitable Agent** — an AI agent over a real Airbnb business that drafts guest
responses (with approval), flags issues, surfaces complaint patterns, runs on a cost-aware inference
layer, and delivers to Telegram. One project, five sequential phases, ~16 weeks, five shipped artifacts.

**Currently in Phase 1 (weeks 1–3): consume the official Hospitable MCP, build nothing.** The official
MCP server is connected to Claude over OAuth (Professional plan, June 2, 2026). The job right now is to
*use it daily on the real property* and take structured notes on what's missing, clunky, or time-saving.
Those notes justify Phases 2–5.

**Two things to carry forward from the kickoff:**
- The Knowledge Hub gap that originally justified Phase 2 is **closed** — the official MCP now ships
  Knowledge Hub read/write tools. Phase 2 needs a new target; decide at week 4 based on Phase 1 notes.
- The "memory system" idea (retrieve past parking answers → draft → approve, severity flagging, weekly
  turnover outlook, Telegram delivery) is **not a new project** — it maps onto Phases 3 + 5, which are
  already planned. Felt the pain in week 1; that's the plan working.

---

## Status Summary

| Milestone | Status |
|---|---|
| Hospitable plan confirmed MCP-eligible (Professional) | ✅ Done |
| Official Hospitable MCP connected to Claude (OAuth) | ✅ Done |
| Connection verified with read-only call (`get-properties`) | ⏳ Next |
| `PHASE1_NOTES.md` note structure set up | ⏳ Next |
| First read-only dry run (turnover outlook query) | ⏳ Next |
| First draft-don't-send guest reply (eval signal logged) | ⏳ This week |
| Daily MCP use + notes (≥ 2 weeks) | ⏳ Phase 1 ongoing |
| Phase 1 blog post drafted incrementally | ⏳ Phase 1 ongoing |
| **Coverage track** — ops manual written | ⏳ Phase 1 parallel |
| **Coverage track** — co-host + backup cleaner lined up | ⏳ Phase 1 parallel |
| **Coverage track** — 5-day unreachable test scheduled | ⏳ Phase 1 parallel |
| HN karma built (for Project 0 Show HN) | ⏳ Phase 1 parallel |
| Phase 2 target re-decided (Knowledge Hub gap closed) | ⏳ Week 4 decision |
| Phases 3–5 | ⏳ Not started |

---

## The Plan — Five Phases

| Phase | Weeks | What | Shipped artifact |
|---|---|---|---|
| 1 | 1–3 | Consume official MCP daily. Build nothing. Requirements discovery on myself. | Blog post: "Two weeks running my Airbnb with Claude + the Hospitable MCP — what works, what doesn't." |
| 2 | 4–6 | **Premise stale — re-target.** Originally a custom Knowledge Hub MCP server; that gap is now closed. New target TBD from Phase 1 notes. | MCP server (scope TBD) + GitHub + blog post |
| 3 | 7–10 | RAG over historical guest messages. Embed + index locally, semantic retrieval, drafts use Knowledge Hub + past-message context. **Hard part: retrieval quality.** | RAG-enabled agent + blog post on chunking, embedding choice, retrieval eval, tradeoffs |
| 4 | 11–13 | Inference routing layer — cheap model (vast.ai serverless) for drafts, Claude for quality, per-response cost tracking. **Hardest phase.** | Cost dashboard + blog post: "Running my Hospitable agent on $X/month" |
| 5 | 14–16 | Eval framework (draft acceptance rate, edit distance) + complaint pattern clustering + severity flagging + Telegram accept/reject delivery. | Eval framework + pattern report + blog post: "Evals for an LLM agent that handles real customers." |

**Definition of "shipped" (per phase, all must be true):** working code on a public GitHub repo with a
clean README; a project page on `samiryuja.dev`; a blog post; at least one cross-post (dev.to, or HN once
karma allows); personally in use where applicable.

**Stop conditions:** plan ends at Phase 5 — resist a Phase 6. If a phase stalls > 2 weeks, ship what
works, write the blog post for what's done, move on. Partial completion with a public artifact beats
perfect completion with none.

---

## Where the "memory system" features land (so they stop nagging)

| Feature I want | Phase | Status |
|---|---|---|
| Draft replies to repeat questions (parking) with my approval | 3 (retrieval) + 5 (approval/eval loop) | Planned |
| Flag issues with severity (green/yellow/orange/red) | 5 (pattern detection) | Planned |
| Weekly turnover outlook (when to call housekeeper) | **Available now** via `get-reservations` + `get-property-calendar` | Use this week |
| Telegram accept/reject delivery | 5 (delivery layer; channel proven in Project 0) | Planned |

Honest caveat: "respond with my approval" is **draft-then-you-send**, not auto-responder. The MCP's
`send-` tools fire immediately with no undo, so the approval gate stays manual by design.

---

## Phase 1 Note-Taking Structure

One file, `PHASE1_NOTES.md`, one entry per real interaction:

```
## YYYY-MM-DD — <what I was trying to do>
- Tools used:
- Time-saving (what genuinely helped):
- Clunky (worked, but friction — note the friction precisely):
- Missing (wanted X, no tool / tool insufficient):   ← Phase 2 justification (higher bar now)
- Would-route-to-cheap-model (drafts that don't need Claude quality):  ← Phase 4 seed
- Eval signal (did I edit the draft? how much?):  ← Phase 5 seed
```

Pre-seeded frictions to log as soon as they recur:
- Retyping the same parking answer → no memory of past replies (Phase 3 justification)
- No severity flagging on incoming messages (Phase 5 justification)
- Turnover schedule has to be asked for manually (near-term win — automate later)
- Want accept/reject over Telegram (Phase 5 delivery)
- Whether the official Knowledge Hub tools are actually *good enough* (sharpens Phase 2 target)

---

## Operational facts about the MCP (learned in setup)

- **Server URL:** `https://mcp.hospitable.com/mcp`, OAuth auth. Works cleanly in Claude (community
  reports OAuth more reliable in Claude than ChatGPT).
- **Plan gate:** MCP is on Host / Professional / Mogul / Legacy. Only Essentials is locked out.
- **`send-` tools deliver immediately** — no draft, no undo. Stay on read tools in Phase 1; draft in chat,
  paste manually.
- **Sessions expire** on long/multi-step workflows (`Server session expired or was not found`). Known
  limitation. Start a fresh conversation to reconnect. **Log it when it bites** — it's real friction the
  Phase 1 post should document honestly.
- **Knowledge Hub tools exist** in the official MCP: `get-property-knowledge-hub`,
  `create/update/delete-knowledge-hub-item`, `delete-knowledge-hub-topic`.

---

## Coverage Track (parallel to Phase 1 — the higher-leverage work)

The single biggest unblocker for higher-comp roles. Phase 1's light build load is the right time.
Without coverage, capped at DC/remote; with it, NYC hybrid / monthly fly-out / relocation open up.

1. **Write the ops manual** — check-in/out, cleaning turnover, property quirks only I know, emergency
   contacts. Can't delegate what isn't written down. Most of it is already in my head / the Knowledge Hub.
2. **Line up two people** — a co-host/backup manager for guest comms, a backup cleaner. The manual makes
   both conversations concrete.
3. **Schedule the 5-day unreachable test** — pick a date, go unreachable, see what breaks. Before week 16,
   not at it.

---

## Phase 1 Blog Post — draft incrementally

Working title: "Two weeks running my Airbnb with Claude + the Hospitable MCP — what works, what doesn't."
Fill as you go so it's 80% done by week 3:
- The setup (~200 words, write this week while fresh)
- The capability surface (what the ~50 tools actually let you do, in my words)
- Three concrete daily-use stories (pulled straight from dated notes — specificity beats generic)
- What's missing or clunky (the credibility anchor; bridge to Phase 2)
- What I'm building next and why (one paragraph, points forward)

---

## Parked Ideas (do NOT start — these are avoidance)

Multi-agent debate framework, standalone inference broker (lives as Phase 4 now), vessel tracking, bills
dashboard (Plaid), plant monitoring, email server, Geiger counter CVE checker. New idea mid-build →
parking lot, default no, two-week rule + current phase shipped before re-evaluating.

---

## Anti-Distraction Reminders (from the strategy doc)

1. One project at a time, shipped with public artifacts, beats eleven unfinished ones.
2. The AI familiarity gap is real but temporary — closes with project work, not study.
3. Underselling my work will cost interviews more than any missing technical skill.

Patterns to watch (named so they can be called out): new project ideas mid-build; tool-swap research;
polishing/refactoring right after a milestone; scope expansion within a phase; productive procrastination
(heavy LeetCode/system-design when there's building to do); aggressive tooling that blows up the evening.

---

## When You Sit Back Down

1. Verify the connection with a read-only call: "List my properties."
2. Run the turnover dry run: "List my reservations for the next 14 days; flag days with a checkout
   followed by a check-in as turnover days."
3. Log that interaction as the first `PHASE1_NOTES.md` entry.
4. Do one draft-don't-send guest reply; log how much you edited it (first Phase 5 data point).
5. Start the ops manual (coverage track) — it's the highest-leverage parallel work.
