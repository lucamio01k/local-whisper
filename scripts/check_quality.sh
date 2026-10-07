#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export MPLCONFIGDIR="${TMPDIR:-/tmp}/local-whisper-test-mpl"
# Unit fixtures must not wait on actual user inference or load user history.
# Lab API tests use temporary roots; process gate covered independently.
LOCAL_WHISPER_LAB_WORKER=1 "$ROOT/.venv/bin/python" -m unittest discover -s tests -v
(cd "$ROOT/frontend" && node --test src/lib/*.test.mjs && npm run build)
