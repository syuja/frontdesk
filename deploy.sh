#!/usr/bin/env bash
set -euo pipefail
export PATH="$(dirname "$(uv python find 3.12)"):$PATH"
uv export --no-hashes --no-dev > requirements.txt
"$(uv python find 3.12)" -m ensurepip --upgrade
if ! "$(uv python find 3.12)" -m pip --version &>/dev/null; then
    echo "ERROR: pip not available on $(uv python find 3.12) — run: $(uv python find 3.12) -m ensurepip --upgrade" >&2
    exit 1
fi
sam build
sam deploy "$@"
