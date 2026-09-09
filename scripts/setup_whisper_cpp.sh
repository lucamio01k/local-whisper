#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_NAME="${1:-small}"
WHISPER_DIR="$ROOT/whisper.cpp"
MODEL_DIR="$ROOT/models_cache/whisper.cpp"

mkdir -p "$MODEL_DIR"

if [ ! -d "$WHISPER_DIR/.git" ]; then
  git clone https://github.com/ggml-org/whisper.cpp.git "$WHISPER_DIR"
fi

cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release
cmake --build "$WHISPER_DIR/build" --config Release -j "$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

"$WHISPER_DIR/models/download-ggml-model.sh" "$MODEL_NAME"
cp "$WHISPER_DIR/models/ggml-$MODEL_NAME.bin" "$MODEL_DIR/ggml-$MODEL_NAME.bin"

echo "whisper.cpp pronto:"
echo "  binario: $WHISPER_DIR/build/bin/whisper-cli"
echo "  modello: $MODEL_DIR/ggml-$MODEL_NAME.bin"
