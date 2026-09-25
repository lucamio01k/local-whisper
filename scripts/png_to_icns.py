#!/usr/bin/env python3
"""Converte PNG quadrato in icona ICNS macOS."""

from pathlib import Path
import sys

from PIL import Image

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
image = Image.open(source).convert("RGBA")
image.save(
    destination,
    format="ICNS",
    sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)],
)
