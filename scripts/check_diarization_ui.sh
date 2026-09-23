#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
node --test "$ROOT/frontend/src/lib/diarizationOptions.test.mjs"
npm --prefix "$ROOT/frontend" run build
