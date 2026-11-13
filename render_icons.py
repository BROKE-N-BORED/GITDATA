#!/usr/bin/env python3
"""Render assets/scout.svg to PNG sizes with QtSvg (build-time only)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

try:
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QSize
except ImportError:
    print("QtSvg not available; cannot render PNGs", file=sys.stderr)
    raise SystemExit(1)

app = QGuiApplication([])
renderer = QSvgRenderer(str(ASSETS / "scout.svg"))
for size in (16, 32, 48, 64, 128, 256):
    image = QImage(QSize(size, size), QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    out = ASSETS / f"scout-{size}.png"
    image.save(str(out))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
# Default icon name used by the .desktop file.
src = ASSETS / "scout-256.png"
dst = ASSETS / "scout.png"
dst.write_bytes(src.read_bytes())
print(f"wrote {dst}")
