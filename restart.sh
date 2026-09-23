#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for port in 8000 5173; do
  for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN || true); do
    process_dir="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    case "$process_dir" in
      "$ROOT"|"$ROOT/"*) kill "$pid" ;;
      *) printf 'Porta %s occupata da processo esterno (PID %s). Nessun arresto.\n' "$port" "$pid" >&2; exit 1 ;;
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
