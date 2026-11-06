# PyInstaller spec: onedir build of the Rule34 Downloader window.
import os
import sys

ROOT = os.path.dirname(SPECPATH)  # SPECPATH is this file's directory (packaging/)
LOGO = os.path.join(ROOT, "rule34_downloader", "gui", "assets", "logo.svg")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(LOGO, "rule34_downloader/gui/assets")],
    hiddenimports=["PySide6.QtSvg"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtPdf",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "tkinter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="rule34-downloader",
    console=False,
    strip=False,
    upx=False,
    icon=os.path.join(SPECPATH, "icons", "rule34-downloader.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="rule34-downloader",
)
