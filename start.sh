#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Finder non carica .zshrc/.zprofile: includi percorsi Homebrew per avvio con doppio clic.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
VENV="$ROOT/.venv"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOG_DIR="$ROOT/logs"
APP_URL="http://localhost:5173"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}[local-whisper]${NC} $*"; }
success() { echo -e "${GREEN}[local-whisper]${NC} $*"; }
warn() { echo -e "${YELLOW}[local-whisper]${NC} $*"; }
error() { echo -e "${RED}[local-whisper]${NC} $*" >&2; }

require_free_port() {
  local port="$1"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    error "Porta $port gia in uso. Chiudi la vecchia finestra Local Whisper o termina il processo che occupa la porta."
    lsof -nP -iTCP:"$port" -sTCP:LISTEN || true
    exit 1
  fi
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempt
  for attempt in {1..40}; do
    if curl --silent --fail --output /dev/null "$url"; then
      return 0
    fi
    sleep 0.25
  done
  error "$label non ha risposto entro 10 secondi. Controlla i log in $LOG_DIR."
  return 1
}

PIDS=()
cleanup() {
  info "Arresto in corso..."
  for pid in "${PIDS[@]+"${PIDS[@]}"}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
  info "Local Whisper e gia in esecuzione: apro il browser."
  if [[ "${LOCAL_WHISPER_NO_BROWSER:-0}" != "1" ]]; then
    open "$APP_URL"
  fi
  exit 0
fi

require_free_port 8000

if [[ ! -x "$VENV/bin/python" || ! -d "$FRONTEND/node_modules" ]]; then
  error "Dipendenze mancanti. Esegui $ROOT/setup.sh"
  exit 1
fi
source "$VENV/bin/activate"

if ! command -v ffmpeg >/dev/null 2>&1; then
  error "ffmpeg non trovato. Installa con: brew install ffmpeg"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  error "npm non trovato. Installa Node.js."
  exit 1
fi

mkdir -p "$LOG_DIR"
: >"$LOG_DIR/backend.log"
: >"$LOG_DIR/frontend.log"

info "Avvio backend FastAPI (porta 8000)..."
cd "$ROOT"
source "$VENV/bin/activate"
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --log-level warning --timeout-graceful-shutdown 10 >"$LOG_DIR/backend.log" 2>&1 &
PIDS+=($!)

wait_for_url "http://127.0.0.1:8000/docs" "Backend"

info "Avvio frontend React (porta 5173)..."
cd "$FRONTEND"
npm run dev >"$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)

wait_for_url "$APP_URL" "Frontend"
if [[ "${LOCAL_WHISPER_NO_BROWSER:-0}" != "1" ]]; then
  open "$APP_URL"
fi

success "App avviata!"
echo ""
echo -e "  ${CYAN}Frontend:${NC} $APP_URL (aperto nel browser)"
echo -e "  ${CYAN}Backend:${NC}  http://localhost:8000"
echo -e "  ${CYAN}API docs:${NC} http://localhost:8000/docs"
echo -e "  ${CYAN}Log backend:${NC} $LOG_DIR/backend.log"
echo -e "  ${CYAN}Log frontend:${NC} $LOG_DIR/frontend.log"
echo ""
echo -e "  Premi ${YELLOW}Ctrl+C${NC} per fermare."
echo ""

wait
