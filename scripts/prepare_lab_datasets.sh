#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ "$(git branch --show-current)" == benchmark-lab ]] || { printf 'Richiesto branch benchmark-lab.\n' >&2; exit 1; }
exec "$ROOT/.venv/bin/python" -m backend.lab_cli prepare-public "$@"
