#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ "$(git branch --show-current)" == benchmark-lab ]] || { printf 'Richiesto branch benchmark-lab.\n' >&2; exit 1; }
if [[ -d "$ROOT/models_cache/lab/laya-multilingual" && -x "$ROOT/.venv-lab/bin/python" ]]; then
  export LOCAL_WHISPER_LAYA_PATH="${LOCAL_WHISPER_LAYA_PATH:-$ROOT/models_cache/lab/laya-multilingual}"
  export LOCAL_WHISPER_SEMANTIC_PYTHON="${LOCAL_WHISPER_SEMANTIC_PYTHON:-$ROOT/.venv-lab/bin/python}"
fi
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export MPLCONFIGDIR="${MPLCONFIGDIR:-$ROOT/lab-data/cache/mpl}"
exec "$ROOT/.venv/bin/python" -m backend.lab_cli "$@"
