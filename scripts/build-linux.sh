#!/usr/bin/env bash
# Build the onedir app with PyInstaller, smoke-test it, and pack a tarball.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${R34_PYTHON:-$ROOT/.venv/bin/python}"

if ! "$PY" -m PyInstaller --version >/dev/null 2>&1; then
    echo "error: PyInstaller is not available in $PY" >&2
    exit 1
fi

"$PY" -m PyInstaller --noconfirm --clean --distpath build/dist --workpath build/work \
    packaging/rule34-downloader.spec

out="$(QT_QPA_PLATFORM=offscreen build/dist/rule34-downloader/rule34-downloader --smoke)"
if ! printf '%s\n' "$out" | grep -qx 'smoke ok'; then
    echo "error: frozen binary smoke test failed" >&2
    exit 1
fi
echo "SMOKE OK (frozen binary)"

VERSION="$("$PY" -c "from rule34_downloader.version import __version__ as v; print(v)")"
TARBALL="build/rule34-downloader-$VERSION-linux-$(uname -m).tar.gz"
tar --owner=0 --group=0 -czf "$TARBALL" -C build/dist rule34-downloader
echo "BUILD OK $TARBALL"
