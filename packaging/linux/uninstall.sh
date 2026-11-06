#!/usr/bin/env bash
# Remove what install.sh created under $PREFIX (default $HOME/.local).
# Never touches the user's settings or download folders.
set -euo pipefail

SIZES=(16 24 32 48 64 128 256 512)
NAME="rule34-downloader"

PREFIX="${PREFIX-$HOME/.local}"
case "$PREFIX" in
    /) echo "error: invalid PREFIX" >&2; exit 2 ;;
    /*) ;;
    *) echo "error: invalid PREFIX" >&2; exit 2 ;;
esac

rm -rf -- "$PREFIX/lib/$NAME"
rm -f "$PREFIX/bin/$NAME"
rm -f "$PREFIX/share/applications/$NAME.desktop"
for n in "${SIZES[@]}"; do
    rm -f "$PREFIX/share/icons/hicolor/${n}x${n}/apps/$NAME.png"
done

echo "removed from $PREFIX"
