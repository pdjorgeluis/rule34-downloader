#!/usr/bin/env bash
# Build the onedir app with PyInstaller, add the installer files, smoke-test it,
# pack a tarball, and check that the tarball installs and uninstalls cleanly.
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

# Put the installer next to the app so the tarball installs on its own.
APP=build/dist/rule34-downloader
rm -rf -- "$APP/install"
mkdir -p "$APP/install/icons"
cp packaging/linux/install.sh packaging/linux/uninstall.sh "$APP/install/"
cp packaging/linux/rule34-downloader.desktop "$APP/install/"
chmod 755 "$APP/install/install.sh" "$APP/install/uninstall.sh"
chmod 644 "$APP/install/rule34-downloader.desktop"
for n in 16 24 32 48 64 128 256 512; do
    cp "packaging/icons/rule34-downloader-$n.png" "$APP/install/icons/"
    chmod 644 "$APP/install/icons/rule34-downloader-$n.png"
done

out="$(QT_QPA_PLATFORM=offscreen build/dist/rule34-downloader/rule34-downloader --smoke)"
if ! printf '%s\n' "$out" | grep -qx 'smoke ok'; then
    echo "error: frozen binary smoke test failed" >&2
    exit 1
fi
echo "SMOKE OK (frozen binary)"

VERSION="$("$PY" -c "from rule34_downloader.version import __version__ as v; print(v)")"
TARBALL="build/rule34-downloader-$VERSION-linux-$(uname -m).tar.gz"
tar --owner=0 --group=0 -czf "$TARBALL" -C build/dist rule34-downloader

# Install self-check: unpack the tarball, install it without arguments into
# temp directories, run it, uninstall it, and require an empty PREFIX.
base="$(dirname "$(mktemp -u)")"
tmp="$(mktemp -d)"
pfx="$(mktemp -d)"
hom="$(mktemp -d)"
clean_tmp() {
    local dir
    for dir in "$tmp" "$pfx" "$hom"; do
        if [ -n "$dir" ]; then
            case "$dir" in
                "$base"/*) rm -rf -- "$dir" ;;
            esac
        fi
    done
}
fail_check() {
    clean_tmp
    echo "error: install check failed: $1" >&2
    exit 1
}
CLEAN_ENV=(env -u QT_QPA_PLATFORM -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_STATE_HOME)

if ! tar -xzf "$TARBALL" -C "$tmp"; then
    fail_check "cannot unpack the tarball"
fi
if ! "${CLEAN_ENV[@]}" HOME="$hom" PREFIX="$pfx" bash "$tmp/rule34-downloader/install/install.sh" >/dev/null; then
    fail_check "install.sh"
fi
if ! "${CLEAN_ENV[@]}" HOME="$hom" PREFIX="$pfx" "$pfx/bin/rule34-downloader" --version | grep -Fx -- "Rule34 Downloader $VERSION" >/dev/null; then
    fail_check "installed binary version line"
fi
if ! "${CLEAN_ENV[@]}" HOME="$hom" PREFIX="$pfx" bash "$tmp/rule34-downloader/install/uninstall.sh" >/dev/null; then
    fail_check "uninstall.sh"
fi
if [ -n "$(find "$pfx" -type f)" ]; then
    fail_check "files left in PREFIX after uninstall"
fi
if [ -n "$(find "$hom" -type f)" ]; then
    fail_check "files written to HOME"
fi
clean_tmp
echo "INSTALL CHECK OK"
echo "BUILD OK $TARBALL"
