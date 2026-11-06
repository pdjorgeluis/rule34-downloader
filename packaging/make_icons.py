"""Render the approved logo into PNG icons and a Windows .ico (Qt, offscreen).

Run with the venv python:  .venv/bin/python packaging/make_icons.py [--out DIR]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

HERE = Path(__file__).resolve().parent
LOGO = HERE.parent / "rule34_downloader" / "gui" / "assets" / "logo.svg"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
NAME = "rule34-downloader"


def render(renderer: QSvgRenderer, size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(0)  # fully transparent
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", metavar="DIR", default=str(HERE / "icons"))
    args = parser.parse_args(argv)
    renderer = QSvgRenderer(str(LOGO))
    if not renderer.isValid():
        print(f"error: cannot read logo: {LOGO}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    targets = [(out / f"{NAME}-{n}.png", n, "PNG") for n in SIZES]
    targets.append((out / f"{NAME}.ico", 256, "ICO"))
    for path, size, fmt in targets:
        if not render(renderer, size).save(str(path), fmt):
            print(f"error: cannot write {path}", file=sys.stderr)
            return 1
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
