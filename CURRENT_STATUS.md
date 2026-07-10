**Immediate (before Week 4 code)**

- Regenerate the Telegram bot token via BotFather `/revoke` — it was exposed in logs earlier and is still pending.
- Create the `hospitable-auditor` repo (separate from samiryuja.dev) with README, gitleaks + osv-scanner, Dependabot.

**Week 4 — Foundation**

- Port the Session 0 `get()` helper into a proper REST client module (auth, pagination, 429 handling).
- Build the 4 deterministic checks, each returning severity-tagged findings:
  - Unanswered inquiry (last `sender_type` = guest)
  - Review actionable (`can_be_sent_now` + checkout date math)
  - Duplicate KH entry (duplicate topic name or duplicate item content within a topic; age-staleness removed)
  - Turnover gap (combined shared-space calendar; gap between checkout and next check-in; severity by tightness; 7-day lookahead)
- Respect the 2/min per-reservation message limit in the inquiry check.

**Week 5 — Delivery**

- Severity tagging + digest formatter (reuse your UTF-16 truncation logic for Telegram).
- Telegram delivery via the new bot token.
- Run locally against the real account; tune thresholds.

**Week 6 — Ship**

- Package Lambda zip, deploy with EventBridge nightly schedule + env vars.
- Run unattended a few nights.
- Project page + blog post + dev.to cross-post.

**Open decisions now resolvable**

- ID cache: **drop it** — not needed.
- LLM checks: **defer to Phase 3** — all 4 v1 checks are deterministic.
- Schedule: nightly is fine (no sub-day-urgent checks in v1).

Start with the token regeneration and repo scaffold — both are quick and unblock everything else. Want the REST client module first, or the repo scaffold?
