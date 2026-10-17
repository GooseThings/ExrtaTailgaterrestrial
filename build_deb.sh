#!/usr/bin/env bash
# build_deb.sh - builds a .deb that installs dish_app.py as a real desktop
# application: a start-menu entry, an icon, and an `extratailgaterrestrial`
# command on PATH. Install/remove it the normal way afterward:
#
#   sudo apt install ./extratailgaterrestrial_<version>_all.deb
#   sudo apt remove extratailgaterrestrial     # or: apt purge, to also drop its venv
#
# The package ships the Python source only, not a pre-built venv (that'd be
# tied to one exact Python build and a couple hundred MB bigger, mostly
# Qt/WebEngine) - its postinst script creates one under
# /opt/extratailgaterrestrial/.venv and installs into it at install time,
# which needs internet access and takes a few minutes the first time.
#
# Usage: ./build_deb.sh [version]   (default: 1.0.0)

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VERSION="${1:-1.0.0}"
PKG=extratailgaterrestrial
OUT="${PKG}_${VERSION}_all.deb"

VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "No $VENV_DIR found - run ./install.sh first (needed to render the icon)." >&2
    exit 1
fi

if [ ! -f packaging/icon.png ]; then
    echo "Rendering icon..."
    "$VENV_DIR/bin/python3" packaging/generate_icon.py
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "Staging package contents in $STAGE..."

mkdir -p "$STAGE/DEBIAN"
sed "s/VERSION_PLACEHOLDER/$VERSION/" packaging/control > "$STAGE/DEBIAN/control"
install -m 755 packaging/postinst "$STAGE/DEBIAN/postinst"
install -m 755 packaging/postrm "$STAGE/DEBIAN/postrm"

APPDIR="$STAGE/opt/$PKG"
mkdir -p "$APPDIR"
cp dish_app.py dish_web.py dish_track.py dish_motion.py dish_calibrate.py \
   dish_image.py dish_scan.py requirements.txt "$APPDIR/"
cp -r templates static "$APPDIR/"

mkdir -p "$STAGE/usr/bin"
install -m 755 packaging/launcher.sh "$STAGE/usr/bin/$PKG"

mkdir -p "$STAGE/usr/share/applications"
install -m 644 packaging/extratailgaterrestrial.desktop "$STAGE/usr/share/applications/"

mkdir -p "$STAGE/usr/share/doc/$PKG"
{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: $PKG"
    echo "Source: https://github.com/GooseThings/ExrtaTailgaterrestrial"
    echo
    echo "Files: *"
    echo "Copyright: GooseThings and contributors"
    echo "License: Unlicense"
    echo
    sed 's/^/ /' LICENSE
} > "$STAGE/usr/share/doc/$PKG/copyright"

echo "Rendering icon sizes..."
for size in 16 24 32 48 64 128 256 512; do
    dir="$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$dir"
    "$VENV_DIR/bin/python3" - "$size" "$dir/$PKG.png" <<'EOF'
import sys
from PIL import Image
size, out = int(sys.argv[1]), sys.argv[2]
Image.open("packaging/icon.png").resize((size, size), Image.LANCZOS).save(out)
EOF
done

# cp/mkdir -p both honor the current umask/source permissions, which can
# leave group-writable files and directories behind depending on the
# caller's shell - dpkg-deb doesn't care, but lintian (correctly) flags
# anything other than the standard 0755/0644 on a shipped file.
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -not -path "*/DEBIAN/*" -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/$PKG"

echo "Building $OUT..."
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"

echo
echo "Done: $OUT"
echo "Install:   sudo apt install ./$OUT"
echo "Remove:    sudo apt remove $PKG        (keeps nothing it created)"
echo "Purge:     sudo apt purge $PKG         (also drops its venv)"
