"""Mint theme: palette constants and the single application style sheet."""
from __future__ import annotations

MINT = "#aae5a3"
WHITE = "#ffffff"
BUTTON_GRAY = "#f0eeef"
BUTTON_BORDER = "#767676"
LINK_BLUE = "#000099"
TEXT = "#000000"
TEAL_DARK = "#0e3d44"
TEAL_NEON = "#3ff3d4"
ERROR_RED = "#990000"
DISABLED_TEXT = "#777777"


def stylesheet() -> str:
    return f"""
QMainWindow, QDialog {{ background: {MINT}; }}
QTabWidget::pane {{ border: 1px solid {BUTTON_BORDER}; background: {MINT}; }}
QWidget#download_tab, QWidget#help_tab {{ background: {MINT}; }}
QTabBar::tab {{ background: {BUTTON_GRAY}; color: {TEXT}; border: 1px solid {BUTTON_BORDER}; padding: 6px 16px; }}
QTabBar::tab:selected {{ background: {WHITE}; }}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QListWidget {{
    background: {WHITE}; color: {TEXT}; border: 1px solid {BUTTON_BORDER};
}}
QPushButton {{
    background: {BUTTON_GRAY}; color: {TEXT}; border: 1px solid {BUTTON_BORDER};
    padding: 5px 14px;
}}
QPushButton:disabled {{ color: {DISABLED_TEXT}; }}
QStatusBar {{ background: {MINT}; color: {TEXT}; }}
QWidget#header {{ background: {TEAL_DARK}; }}
QLabel#header_title {{ color: {TEAL_NEON}; font-size: 22px; font-weight: bold; background: transparent; }}
QLabel#header_logo {{ background: transparent; }}
QLabel#options_link {{ color: {LINK_BLUE}; }}
QLabel#error_label {{ color: {ERROR_RED}; }}
QGroupBox {{ color: {TEXT}; border: 1px solid {BUTTON_BORDER}; margin-top: 8px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {TEXT}; }}
QCheckBox, QRadioButton {{ color: {TEXT}; }}
QLabel#query_preview, QLabel#dest_preview {{
    background: {WHITE}; border: 1px solid {BUTTON_BORDER}; padding: 4px;
}}
QPushButton#start_button {{ font-weight: bold; }}
"""
