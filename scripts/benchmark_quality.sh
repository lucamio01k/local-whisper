#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT"
export HF_HUB_OFFLINE=1
"$ROOT/.venv/bin/python" "$ROOT/scripts/evaluate_quality.py" prepare
"$ROOT/.venv/bin/python" "$ROOT/scripts/evaluate_quality.py" run "$@"
"$ROOT/.venv/bin/python" "$ROOT/scripts/evaluate_quality.py" diarize
