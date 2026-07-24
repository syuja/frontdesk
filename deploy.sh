#!/usr/bin/env bash
set -euo pipefail
command -v uv >/dev/null || { echo "uv not found on PATH — see https://docs.astral.sh/uv/" >&2; exit 1; }
export PATH="$(dirname "$(uv python find 3.12)"):$PATH"
uv export --no-hashes --no-dev > requirements.txt
if ! "$(uv python find 3.12)" -m pip --version >/dev/null 2>&1; then
  "$(uv python find 3.12)" -m ensurepip --upgrade || {
    echo "pip missing on uv's 3.12 and ensurepip failed — try 'sam build --use-container'" >&2
    exit 1
  }
fi
sam build
sam deploy "$@"
