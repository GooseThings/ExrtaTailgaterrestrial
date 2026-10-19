#!/usr/bin/env bash
# build_standalone.sh - bundle dish_web.py and its templates/static assets
# into a single, dependency-free executable via PyInstaller, so the web UI
# can run on a machine with no Python/pip setup at all.
#
# The result still needs to be run next to (or told where to find) the
# usual per-dish state files - observer_location.json, track_settings.json,
# pointing_correction.json, space_track_credentials.json, tle_cache.json -
# since it reads/writes those relative to whatever directory it's run from,
# same as running from source.
#
# Linux build. PyInstaller doesn't cross-compile; the Windows package is a
# separate installer, built on Windows by packaging/windows/build.ps1. The
# release CI (.github/workflows/release-packages.yml) builds both.
#
# Usage: ./build_standalone.sh
# Output: dist/dish_web (run it directly, no venv/activation needed)

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "No $VENV_DIR found - run ./install.sh first." >&2
    exit 1
fi

PYTHON="$VENV_DIR/bin/python3"
"$PYTHON" -c "import PyInstaller" 2>/dev/null || {
    echo "Installing PyInstaller (build-only - not a runtime dependency, so it's not in requirements.txt)..."
    "$VENV_DIR/bin/pip" install --quiet pyinstaller
}

echo "Building..."
"$PYTHON" -m PyInstaller --onefile --name dish_web \
    --add-data "templates:templates" \
    --add-data "static:static" \
    dish_web.py

echo
echo "Done: dist/dish_web"
echo "Copy it anywhere and run it directly - no Python install needed on that machine."
