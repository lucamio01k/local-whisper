#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCES="$ROOT/Avvia Local Whisper.app/Contents/Resources"
SOURCE="$RESOURCES/LocalWhisper-source.png"
OUTPUT="$RESOURCES/LocalWhisper.icns"

mkdir -p "$RESOURCES"
magick -size 1024x1024 xc:none \
  -fill '#6d28d9' -draw 'roundrectangle 72,72 952,952 210,210' \
  -fill '#f5f3ff' -draw 'roundrectangle 365,225 659,717 147,147' \
  -fill '#6d28d9' -draw 'rectangle 365,510 659,717' \
  -stroke '#f5f3ff' -strokewidth 52 -fill none \
  -draw 'roundrectangle 280,450 744,799 232,232' \
  -stroke none -fill '#6d28d9' -draw 'rectangle 240,400 784,567' \
  -stroke '#f5f3ff' -strokewidth 52 \
  -draw 'line 512,799 512,895' \
  -draw 'line 409,895 615,895' \
  -depth 8 \
  "$SOURCE"

python3 "$ROOT/scripts/png_to_icns.py" "$SOURCE" "$OUTPUT"
rm -f "$SOURCE"
echo "Creata icona: $OUTPUT"
