#!/usr/bin/env bash
# install.sh - set up a Python virtual environment with everything this
# project needs, and flag the one system-level gotcha (serial port group
# membership) that otherwise shows up as a confusing permission error the
# first time a script tries to open the dish.
#
# Usage: ./install.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON=${PYTHON:-python3}
VENV_DIR=".venv"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "python3 not found - install it first (e.g. 'sudo apt install python3 python3-venv')." >&2
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR..."
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "Using existing virtual environment in $VENV_DIR."
fi

echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r requirements.txt

# On Debian/Ubuntu, the dish shows up as /dev/ttyACM0 owned by root:dialout -
# a user not in that group gets a silent permission-denied the first time
# any script here tries to open the port, which is a confusing place to
# discover it. Advisory only: this just prints what to do rather than
# running sudo itself.
if command -v getent >/dev/null 2>&1 && getent group dialout >/dev/null 2>&1; then
    if ! id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx dialout; then
        echo
        echo "NOTE: '$USER' is not in the 'dialout' group, needed to open /dev/ttyACM0."
        echo "      sudo usermod -aG dialout $USER"
        echo "      (then log out and back in for it to take effect)"
    fi
fi

echo
echo "Done. Start the web UI with:"
echo "  $VENV_DIR/bin/python3 dish_web.py"
echo "then open http://localhost:5000"
