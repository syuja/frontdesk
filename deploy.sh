#!/usr/bin/env bash
set -euo pipefail
export PATH="$(dirname "$(uv python find 3.12)"):$PATH"
uv export --no-hashes --no-dev > requirements.txt
sam build
sam deploy "$@"
