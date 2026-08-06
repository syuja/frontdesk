#!/usr/bin/env bash
set -euo pipefail
command -v uv >/dev/null || { echo "uv not found on PATH — see https://docs.astral.sh/uv/" >&2; exit 1; }
export PATH="$(dirname "$(uv python find 3.12)"):$PATH"

source .env
: "${HOSPITABLE_PAT:?HOSPITABLE_PAT not set in .env}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set in .env}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set in .env}"

uv export --no-hashes --no-dev > requirements.txt
if ! "$(uv python find 3.12)" -m pip --version >/dev/null 2>&1; then
  "$(uv python find 3.12)" -m ensurepip --upgrade || {
    echo "pip missing on uv's 3.12 and ensurepip failed — try 'sam build --use-container'" >&2
    exit 1
  }
fi
sam build
sam deploy \
  --parameter-overrides \
    HospitablePat="$HOSPITABLE_PAT" \
    TelegramBotToken="$TELEGRAM_BOT_TOKEN" \
    TelegramChatId="$TELEGRAM_CHAT_ID" \
  "$@"
