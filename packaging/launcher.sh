#!/bin/sh
# Installed as /usr/bin/extratailgaterrestrial by the .deb package.
#
# /opt/extratailgaterrestrial holds the read-only program files (owned by
# root, managed by dpkg) - dish_web.py's state files (observer_location.json,
# track_settings.json, saved scans, calibration results, etc.) are plain
# files written relative to whatever directory it's run from, so launching
# it there directly would scatter per-user data into root-owned program
# files, and clash if more than one person uses the same machine. Each user
# instead gets their own state directory here, matching the XDG Base
# Directory spec.
set -e

STATE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/extratailgaterrestrial"
mkdir -p "$STATE_DIR"
cd "$STATE_DIR"

exec /opt/extratailgaterrestrial/.venv/bin/python3 /opt/extratailgaterrestrial/dish_app.py "$@"
