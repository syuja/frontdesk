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
- [ ] **Duplicate Knowledge Hub entry** — topic name appearing more than once (case-insensitive), or duplicate item content within a topic
- [ ] **Turnover gap** — combined shared-space calendar; gap between a checkout and the next check-in; severity scaled by tightness (CRITICAL=same-day, HIGH=1d, MEDIUM=2–3d, LOW=4+d); 7-day lookahead

---

## Setup

```bash
uv sync          # install dependencies into .venv
cp .env.example .env   # fill in PAT + Telegram creds
uv run -m hospitable   # smoke-test locally
```

## Deploy

`requirements.txt` is a generated artifact — regenerate it before every build:

```bash
uv export --no-hashes --no-dev > requirements.txt && sam build && sam deploy
```

**First deploy (one-time):** run the guided flow to create the CloudFormation stack,
the managed S3 artifact bucket, and `samconfig.toml`:

```bash
uv export --no-hashes --no-dev > requirements.txt
sam build
sam deploy --guided \
  --parameter-overrides \
    HospitablePat=<pat> \
    TelegramBotToken=<token> \
    TelegramChatId=<chat_id>
```

`samconfig.toml` is safe to commit — it stores stack name, region, and bucket name.
**Never put secrets in `samconfig.toml` or `template.yaml`.**
Subsequent deploys: re-run the one-liner above; `sam deploy` reads `samconfig.toml`
and prompts for `--parameter-overrides` if omitted.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `HOSPITABLE_PAT` | yes | Hospitable public API personal access token |
| `TELEGRAM_BOT_TOKEN` | yes | Telegram bot token (`bot<token>` format from BotFather) |
| `TELEGRAM_CHAT_ID` | no | Destination chat/channel ID (defaults to the auditor channel) |

Local dev: set these in `.env` (excluded from git). Lambda: supplied via `--parameter-overrides` at deploy time; CloudFormation injects them as environment variables.
