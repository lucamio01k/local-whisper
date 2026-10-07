#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$ROOT/lab-data/cache/mpl}"
if [[ "$(git -C "$ROOT" branch --show-current)" != "benchmark-lab" ]]; then
  printf 'Lab richiede branch benchmark-lab. Nessun riavvio.\n' >&2
  exit 1
fi
# Reuse installed optional local runtime/weights; explicit overrides preserved.
if [[ -d "$ROOT/models_cache/lab/laya-multilingual" && -x "$ROOT/.venv-lab/bin/python" ]]; then
  export LOCAL_WHISPER_LAYA_PATH="${LOCAL_WHISPER_LAYA_PATH:-$ROOT/models_cache/lab/laya-multilingual}"
  export LOCAL_WHISPER_SEMANTIC_PYTHON="${LOCAL_WHISPER_SEMANTIC_PYTHON:-$ROOT/.venv-lab/bin/python}"
fi
if [[ -n "${LOCAL_WHISPER_LAYA_PATH:-}" || -n "${LOCAL_WHISPER_SEMANTIC_PYTHON:-}" ]]; then
  for port in 8000 5173; do
    for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN || true); do
      process_dir="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
      case "$process_dir" in
        "$ROOT"|"$ROOT/"*) kill "$pid" ;;
        *) printf 'Porta %s occupata da processo esterno. Nessun arresto.\n' "$port" >&2; exit 1 ;;
      esac
    done
  done
  for attempt in {1..30}; do
    if ! lsof -tiTCP:8000 -sTCP:LISTEN >/dev/null 2>&1 && ! lsof -tiTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
      exec "$ROOT/start.sh"
    fi
    sleep 0.5
  done
  printf 'Arresto ancora in corso. Riprova dopo completamento.\n' >&2
  exit 1
fi
exec "$ROOT/restart.sh"
