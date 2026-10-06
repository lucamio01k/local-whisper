#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export MPLCONFIGDIR="${TMPDIR:-/tmp}/local-whisper-test-mpl"
"$ROOT/.venv/bin/python" -m unittest discover -s tests -v
(cd "$ROOT/frontend" && node --test src/lib/*.test.mjs && npm run build)
