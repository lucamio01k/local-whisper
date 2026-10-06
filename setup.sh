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
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  if [[ ! -x .venv-qwen/bin/python ]]; then
    if command -v uv >/dev/null 2>&1; then
      uv venv --python 3.12 .venv-qwen
    else
      python3.12 -m venv .venv-qwen
    fi
  fi
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$ROOT/.venv-qwen/bin/python" 'mlx-audio[stt]==0.5.6'
  else
    .venv-qwen/bin/python -m pip install 'mlx-audio[stt]==0.5.6'
  fi
fi
(cd frontend && npm ci)
printf 'Setup completato. Avvio: %s/start.sh\n' "$ROOT"
