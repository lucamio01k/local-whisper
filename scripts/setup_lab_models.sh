#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -x "$ROOT/.venv-lab/bin/python" ]]; then
  "$ROOT/.venv/bin/python" -m venv "$ROOT/.venv-lab"
fi
"$ROOT/.venv-lab/bin/python" -m pip install -r "$ROOT/backend/requirements-lab.txt"
HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 "$ROOT/.venv-lab/bin/python" "$ROOT/scripts/download_lab_models.py"
printf 'Avvio Lab con Laya:\nLOCAL_WHISPER_SEMANTIC_PYTHON="%s/.venv-lab/bin/python" LOCAL_WHISPER_LAYA_PATH="%s/models_cache/lab/laya-multilingual" "%s/scripts/restart_lab.sh"\n' "$ROOT" "$ROOT" "$ROOT"
