#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

clear
echo "[local-whisper] Avvio da: $ROOT"
echo "[local-whisper] Premi Ctrl+C per fermare backend e frontend."
echo

./start.sh
status=$?

echo
if [ "$status" -ne 0 ]; then
  echo "[local-whisper] Avvio fallito con codice: $status"
else
  echo "[local-whisper] Sessione terminata."
fi

echo
read -r -p "Premi Invio per chiudere questa finestra..."
