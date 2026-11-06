"""Application entry: create_app() and main()."""
from __future__ import annotations

import argparse
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from . import theme
from .main_window import MainWindow


def _palette() -> QPalette:
    pal = QPalette()
    for role, color in (
        (QPalette.Window, theme.MINT),
        (QPalette.WindowText, theme.TEXT),
        (QPalette.Base, theme.WHITE),
        (QPalette.Text, theme.TEXT),
        (QPalette.Button, theme.BUTTON_GRAY),
        (QPalette.ButtonText, theme.TEXT),
        (QPalette.Link, theme.LINK_BLUE),
    ):
        pal.setColor(role, QColor(color))
    return pal


def create_app(argv=None) -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(list(sys.argv if argv is None else argv))
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setStyleSheet(theme.stylesheet())
    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rule34_downloader.gui")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--screenshot", metavar="PATH")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    app = create_app([sys.argv[0]] if argv is None else ["rule34_downloader.gui"])
    window = MainWindow()
    window.show()
    if args.smoke or args.screenshot:
        app.processEvents()
        if args.screenshot:
            for _ in range(2):  # let the status bar text paint
                app.processEvents()
            if not window.grab().save(args.screenshot, "PNG"):
                print(f"error: cannot write screenshot: could not save {args.screenshot}",
                      file=sys.stderr)
                return 1
        if args.smoke:
            print("smoke ok")
        return 0
    return app.exec()
