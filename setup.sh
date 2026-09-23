#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 .venv
  else
    python3.12 -m venv .venv
  fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip sync --python "$ROOT/.venv/bin/python" "$ROOT/backend/requirements.lock.txt"
else
  .venv/bin/python -m pip install -r backend/requirements.lock.txt
fi
(cd frontend && npm ci)
printf 'Setup completato. Avvio: %s/start.sh\n' "$ROOT"
