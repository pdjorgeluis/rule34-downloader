#!/usr/bin/env bash
# Install a built Rule34 Downloader directory under $PREFIX (default $HOME/.local).
set -euo pipefail

usage() {
    echo "usage: install.sh <path to the built rule34-downloader directory>" >&2
    exit 2
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$HERE/icons" ]; then
    ICON_SRC="$HERE/icons"
else
    ICON_SRC="$HERE/../icons"
fi
SIZES=(16 24 32 48 64 128 256 512)
NAME="rule34-downloader"

if [ $# -ge 1 ]; then
    built="$1"
elif [ -f "$HERE/../$NAME" ] && [ -x "$HERE/../$NAME" ]; then
    built="$(cd "$HERE/.." && pwd)"
else
    usage
fi
[ -f "$built/$NAME" ] && [ -x "$built/$NAME" ] || usage

PREFIX="${PREFIX-$HOME/.local}"
case "$PREFIX" in
    /) echo "error: invalid PREFIX" >&2; exit 2 ;;
    /*) ;;
    *) echo "error: invalid PREFIX" >&2; exit 2 ;;
esac

mkdir -p "$PREFIX/lib" "$PREFIX/bin" "$PREFIX/share/applications"
for n in "${SIZES[@]}"; do
    mkdir -p "$PREFIX/share/icons/hicolor/${n}x${n}/apps"
done

rm -rf -- "$PREFIX/lib/$NAME"
mkdir -p "$PREFIX/lib/$NAME"
cp -a "$built"/. "$PREFIX/lib/$NAME/"
ln -sfn "$PREFIX/lib/$NAME/$NAME" "$PREFIX/bin/$NAME"

exec_path="$PREFIX/bin/$NAME"
exec_path="${exec_path//%/%%}"
case "$exec_path" in
    *" "*) exec_path="\"$exec_path\"" ;;
esac
tpl="$(<"$HERE/$NAME.desktop")"
printf '%s\n' "${tpl//@EXEC@/$exec_path}" > "$PREFIX/share/applications/$NAME.desktop"

for n in "${SIZES[@]}"; do
    cp "$ICON_SRC/$NAME-$n.png" "$PREFIX/share/icons/hicolor/${n}x${n}/apps/$NAME.png"
done

echo "installed to $PREFIX"
