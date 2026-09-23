#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/models_cache/whisper.cpp"
mkdir -p "$DEST"
fetch() {
  local name="$1" url="$2"
  if [[ ! -s "$DEST/$name" ]]; then
    curl --fail --location --retry 3 --output "$DEST/$name.part" "$url"
    mv "$DEST/$name.part" "$DEST/$name"
  fi
}
fetch ggml-large-v3.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
fetch ggml-silero-v6.2.0.bin https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin
