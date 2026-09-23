#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# Re-run local evaluation; replaces benchmark artifacts only.
"$ROOT/.venv/bin/python" "$HERE/benchmark.py"
HF_HUB_OFFLINE=1 "$ROOT/.venv/bin/python" "$HERE/diarization_probe.py" >"$HERE/diarization_probe.log" 2>&1
"$ROOT/whisper.cpp/build/bin/whisper-cli" -m "$ROOT/models_cache/whisper.cpp/ggml-large-v3-turbo.bin" -f "$ROOT/uploads/b12cfe14-e01a-41a6-afac-d18c31eb7046/whisper_cpp.wav" -t 8 -l it -bs 3 -d 60000 -ojf -of "$HERE/token_probe" >"$HERE/token_probe.log" 2>&1
"$ROOT/.venv/bin/python" "$HERE/audit.py"
