# dish_web.py
# Browser-based control panel for the Tailgater dish: scan, view saved
# scans as heatmaps, and track satellites — all from one local web page.
#
# Requirements: pip install -r requirements.txt
#
# Usage: python3 dish_web.py
#        then open http://localhost:5000 in a browser

import glob
import io
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
import regex as re
import serial
from flask import Flask, jsonify, request, send_file, render_template
from PIL import Image

import dish_calibrate
import dish_image
import dish_motion
import dish_track

app = Flask(__name__)
# This app's static/*.js and templates get edited and the server restarted
# often during development - without this, browsers keep serving a stale
# cached app.js after a restart even on a plain reload.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.before_request
def _reject_cross_origin_mutations():
    # There's no auth at all here (local tool, bound to 127.0.0.1) - every
    # POST route (reboot, disconnect, start a scan/track, move the dish...)
    # is otherwise reachable by a plain CSRF form from any other tab/site
    # the browser has open, since the browser will happily submit it to
    # localhost. A classic <form> attack can't set the Origin header, so
    # requiring it to match this server's own origin blocks that without
    # needing any session/token machinery. Our own frontend only ever
    # talks to this API via fetch() from this same page, which always
    # sends Origin, so this never affects normal use.
    if request.method != "POST":
        return
    origin = request.headers.get("Origin")
    if origin is None or origin.rstrip('/') != request.host_url.rstrip('/'):
        return jsonify({"ok": False, "error": "Cross-origin requests are not allowed."}), 403


BAUD_RATE = 9600
OBSERVER_FILE = "observer_location.json"
CORRECTION_FILE = "pointing_correction.json"
TRACK_SETTINGS_FILE = "track_settings.json"
TLE_CACHE_FILE = "tle_cache.json"
# Not committed - create it by hand (see README) with your own free
# space-track.org account. Kept out of the request/response cycle
# entirely so the credentials never pass through chat/logs.
SPACE_TRACK_CREDENTIALS_FILE = "space_track_credentials.json"

# Sources picked specifically for one constellation should always show
# it, regardless of the "show constellations" setting that otherwise
# filters Starlink/OneWeb/etc. out of the general lists.
DEDICATED_CONSTELLATION_SOURCES = {"Celestrak - Starlink", dish_track.SPACE_TRACK_STARLINK_SOURCE_NAME}


def _load_space_track_credentials():
    try:
        with open(SPACE_TRACK_CREDENTIALS_FILE) as f:
            data = json.load(f)
        username, password = data["username"], data["password"]
        if username and password:
            return username, password
    except (OSError, ValueError, KeyError):
        pass
    return None


def _load_observer():
    try:
        with open(OBSERVER_FILE) as f:
            data = json.load(f)
            return {"lat": float(data["lat"]), "lon": float(data["lon"])}
    except (OSError, ValueError, KeyError):
        return {"lat": dish_track.OBSERVER_LAT, "lon": dish_track.OBSERVER_LON}


def _save_observer(lat, lon):
    with open(OBSERVER_FILE, "w") as f:
        json.dump({"lat": lat, "lon": lon}, f)


def _load_correction():
    try:
        with open(CORRECTION_FILE) as f:
            data = json.load(f)
            return {"az": float(data["az"]), "el": float(data["el"])}
    except (OSError, ValueError, KeyError):
        return {"az": 0.0, "el": 0.0}


def _save_correction(az, el):
    with open(CORRECTION_FILE, "w") as f:
        json.dump({"az": az, "el": el}, f)


def _load_track_settings():
    defaults = {"min_elevation": dish_track.MIN_ELEVATION, "max_distance_km": None, "show_constellations": False}
    try:
        with open(TRACK_SETTINGS_FILE) as f:
            data = json.load(f)
            defaults["min_elevation"] = float(data["min_elevation"])
            max_distance_km = data.get("max_distance_km")
            defaults["max_distance_km"] = float(max_distance_km) if max_distance_km is not None else None
            defaults["show_constellations"] = bool(data.get("show_constellations", False))
    except (OSError, ValueError, KeyError):
        pass
    return defaults


def _save_track_settings(min_elevation, max_distance_km, show_constellations):
    with open(TRACK_SETTINGS_FILE, "w") as f:
        json.dump({
            "min_elevation": min_elevation,
            "max_distance_km": max_distance_km,
            "show_constellations": show_constellations,
        }, f)


def _load_tle_cache():
    """Returns (source, fetched_at, sats) from the last successful TLE
    fetch, or None if there isn't one yet."""
    try:
        with open(TLE_CACHE_FILE) as f:
            data = json.load(f)
        sats = [(s[0], s[1], s[2]) for s in data["satellites"]]
        return data["source"], float(data["fetched_at"]), sats
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def _save_tle_cache(source, sats, fetched_at):
    with open(TLE_CACHE_FILE, "w") as f:
        json.dump({
            "source": source,
            "fetched_at": fetched_at,
            "satellites": [[name, l1, l2] for name, l1, l2 in sats],
        }, f)

# ────────────────────────────────────────────────────────────────
# SHARED STATE
# ────────────────────────────────────────────────────────────────

state_lock = threading.Lock()

# Serial port is single-owner: exactly one of a manual move, a scan, or a
# tracking session may be using it at a time. Flag checks under state_lock
# aren't enough on their own (two requests can both pass the check before
# either starts moving), so this lock is held for the full duration of
# whichever operation owns the dish.
dish_io_lock = threading.Lock()

state = {
    "dish": None,
    "port": "/dev/ttyACM0",

    "scan_thread": None,
    "scan_stop": None,
    "scan": {
        "running": False,
        "azimuth": None,
        "elevation": None,
        "signal": None,
        "done": 0,
        "total": 0,
        "log": [],
        "last_file": None,
        "error": None,
    },
    "scan_preview_png": None,

    "calibrate_thread": None,
    "calibrate_stop": None,
    "calibrate": {
        "running": False,
        "current_target": None,
        "azimuth": None,
        "elevation": None,
        "signal": None,
        "done": 0,
        "total": 0,
        "log": [],
        "results": [],
        "last_file": None,
        "error": None,
    },

    "observer": _load_observer(),
    "correction": _load_correction(),
    "track_settings": _load_track_settings(),

    "satellites": [],
    "all_satellites": [],  # unfiltered - "satellites" is this minus constellation sats, unless shown
    "track_thread": None,
    "track": {
        "running": False,
        "message": "Not tracking",
        "below_horizon": False,
        "tle_fetched_at": None,
        "tle_source": None,
    },

    "motion": {
        "step_size": dish_motion.DEFAULT_STEP_SIZE,
        "tolerance": dish_motion.DEFAULT_TOLERANCE,
        "warnings": [],
        "position": {"az": None, "el": None},
    },
}

# Preload whatever TLE data was last successfully fetched, so a restart
# doesn't leave the Track tab empty until the user re-pulls it by hand.
_cached_tle = _load_tle_cache()
if _cached_tle is not None:
    _cached_source, _cached_fetched_at, _cached_sats = _cached_tle
    state["all_satellites"] = _cached_sats
    _allow_constellations = (
        state["track_settings"]["show_constellations"]
        or _cached_source in DEDICATED_CONSTELLATION_SOURCES
    )
    _filtered = _cached_sats if _allow_constellations else [
        s for s in _cached_sats if not dish_track.is_constellation_sat(s[0])
    ]
    _ranked, _ = dish_track.rank_by_next_pass(
        _filtered, state["observer"]["lat"], state["observer"]["lon"],
        horizon_deg=state["track_settings"]["min_elevation"],
    )
    state["satellites"] = [(r["name"], r["l1"], r["l2"]) for r in _ranked]
    state["track"]["tle_fetched_at"] = _cached_fetched_at
    state["track"]["tle_source"] = _cached_source


def log_scan(message):
    log = state["scan"]["log"]
    log.append(message)
    del log[:-200]  # keep the last 200 lines


def log_calibrate(message):
    log = state["calibrate"]["log"]
    log.append(message)
    del log[:-200]  # keep the last 200 lines


# ────────────────────────────────────────────────────────────────
# MOTION (stepped/verified moves + slip warnings)
# ────────────────────────────────────────────────────────────────

def _record_motion_warning(warning):
    with state_lock:
        warnings = state["motion"]["warnings"]
        warnings.append(warning)
        del warnings[:-50]  # keep the last 50


def _invalidate_position(axis):
    """Mark an axis's tracked position estimate unknown - use this whenever
    the dish is moved by something other than _safe_move/_verify_position
    (relative nudges, a fresh connection, etc.) so the next tracked move
    isn't fooled by a stale cached value into skipping a real move."""
    with state_lock:
        state["motion"]["position"][axis] = None


def _record_track_position(az, el):
    # TrackingThread drives the dish via dish_motion.stepped_move directly
    # rather than through _safe_move (it has its own az/el continuity
    # bookkeeping independent of _safe_move's), so without this the
    # commanded-position status shown in the UI would never update while
    # a track session is running.
    with state_lock:
        state["motion"]["position"]["az"] = az
        state["motion"]["position"]["el"] = el


def _safe_move(dish, axis, target_deg, cancel_event=None):
    """Stepped, verified move using the current motion settings. Falls back
    to a single direct, forced move when an axis's position is unknown
    (e.g. right after a server restart), rather than assuming we're
    already there."""
    with state_lock:
        step_size = state["motion"]["step_size"]
        tolerance = state["motion"]["tolerance"]
        current = state["motion"]["position"].get(axis)

    unknown = current is None
    if unknown:
        current = target_deg

    achieved = dish_motion.stepped_move(
        dish, axis, current, target_deg,
        step_size=step_size, tolerance=tolerance,
        on_warning=_record_motion_warning,
        force=unknown, cancel_event=cancel_event,
    )

    with state_lock:
        state["motion"]["position"][axis] = achieved
    return achieved


def _verify_position(dish, axis, expected_deg, cancel_event=None):
    """Re-affirm position at a serpentine direction-reversal point."""
    with state_lock:
        tolerance = state["motion"]["tolerance"]
    return dish_motion.verify_position(
        dish, axis, expected_deg, tolerance=tolerance,
        on_warning=_record_motion_warning, cancel_event=cancel_event,
    )


# ────────────────────────────────────────────────────────────────
# DISH SERIAL HELPERS (mirrors dish_scan.py's proven char-by-char protocol)
# ────────────────────────────────────────────────────────────────

def send_command(dish, text):
    for ch in text:
        dish.write(ch.encode())
    dish.write(b'\r')


def read_signal_strength(dish, stop_event):
    while not stop_event.is_set():
        try:
            dish.reset_input_buffer()
            send_command(dish, "rfwatch 1")
            dish.flush()
            dish.reset_output_buffer()
            reply = dish.read(207).decode().strip()
            header, *readings = reply.split('[5D')
            output = readings[0]
            output = re.sub(r'\p{C}', '', output)
            output = re.sub(r'[^\d]', '', output).strip()
            return int(output)
        except serial.SerialException:
            time.sleep(0.1)
            continue
    return None


# ────────────────────────────────────────────────────────────────
# CONNECTION
# ────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    with state_lock:
        dish = state["dish"]
        now = time.time()
        warnings = state["motion"]["warnings"]
        recent = [w for w in warnings if now - w["time"] < 15]
        return jsonify({
            "connected": bool(dish and dish.is_open),
            "port": state["port"],
            "scan": {k: v for k, v in state["scan"].items() if k != "log"},
            "calibrate": {k: v for k, v in state["calibrate"].items() if k != "log"},
            "track": state["track"],
            "motion": {
                "step_size": state["motion"]["step_size"],
                "tolerance": state["motion"]["tolerance"],
                "slip_active": bool(recent),
                "warnings": list(warnings[-20:]),
                "position": dict(state["motion"]["position"]),
            },
        })


@app.route("/api/motion/settings", methods=["GET", "POST"])
def api_motion_settings():
    if request.method == "POST":
        p = request.json or {}
        with state_lock:
            if "step_size" in p:
                state["motion"]["step_size"] = max(1.0, min(45.0, float(p["step_size"])))
            if "tolerance" in p:
                state["motion"]["tolerance"] = max(5, min(500, int(p["tolerance"])))
    with state_lock:
        return jsonify({
            "step_size": state["motion"]["step_size"],
            "tolerance": state["motion"]["tolerance"],
        })


@app.route("/api/motion/clear", methods=["POST"])
def api_motion_clear():
    with state_lock:
        state["motion"]["warnings"] = []
    return jsonify({"ok": True})


@app.route("/api/move", methods=["POST"])
def api_move():
    p = request.json or {}
    axis = p.get("axis")
    if axis not in ("az", "el"):
        return jsonify({"ok": False, "error": "axis must be 'az' or 'el'"}), 400
    try:
        target = float(p["degrees"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"ok": False, "error": "degrees required"}), 400

    with state_lock:
        dish = state["dish"]
        if not dish or not dish.is_open:
            return jsonify({"ok": False, "error": "Connect to the dish first."}), 400

    if not dish_io_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Dish is busy with another operation."}), 409
    try:
        achieved = _safe_move(dish, axis, target)
    finally:
        dish_io_lock.release()
    return jsonify({"ok": True, "target_deg": achieved})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    port = (request.json or {}).get("port", state["port"])
    with state_lock:
        if state["dish"] and state["dish"].is_open:
            state["dish"].close()
        try:
            dish = serial.Serial(
                port=port,
                baudrate=BAUD_RATE,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=1,
            )
        except serial.SerialException as e:
            state["dish"] = None
            # The raw exception (OS error text, full device path) isn't
            # shown to the client - this binds to 127.0.0.1 but could be
            # exposed to others on a LAN (e.g. shared over wifi at an
            # actual tailgate), and CodeQL correctly flags returning
            # exception text as information exposure (py/stack-trace-exposure).
            print(f"[connect] {e}", file=sys.stderr)
            return jsonify({"ok": False, "error": f"Could not open serial port {port}."}), 400
        state["dish"] = dish
        state["port"] = port
    _invalidate_position('az')
    _invalidate_position('el')
    return jsonify({"ok": True})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    _stop_scan()
    _stop_track()
    _stop_calibrate()
    with state_lock:
        if state["dish"] and state["dish"].is_open:
            state["dish"].close()
        state["dish"] = None
    _invalidate_position('az')
    _invalidate_position('el')
    return jsonify({"ok": True})


def _restart_process():
    # Give the HTTP response below time to actually reach the browser
    # before tearing this process down.
    #
    # This spawns a successor and then hard-exits, rather than
    # os.execv-ing in place: Werkzeug's listening socket turned out to
    # survive execv (it's inheritable, and exec doesn't close inheritable
    # fds), leaving an orphaned copy still bound to the port with no
    # Python-level handle left to close it - the re-exec'd process would
    # then fail to rebind until the *original* process fully exited.
    # subprocess.Popen defaults to close_fds=True, so the successor starts
    # with no inherited sockets, and os._exit() (not sys.exit(), which
    # only unwinds this one thread) immediately tears down every fd this
    # process holds, freeing the port for the successor to bind.
    time.sleep(1.0)
    subprocess.Popen(
        [sys.executable] + sys.argv,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os._exit(0)


@app.route("/api/system/reboot", methods=["POST"])
def api_system_reboot():
    _stop_scan()
    _stop_track()
    with state_lock:
        if state["dish"] and state["dish"].is_open:
            state["dish"].close()
        state["dish"] = None
    threading.Thread(target=_restart_process, daemon=False).start()
    return jsonify({"ok": True})


# ────────────────────────────────────────────────────────────────
# SCAN
# ────────────────────────────────────────────────────────────────

@app.route("/api/scan/estimate", methods=["POST"])
def api_scan_estimate():
    p = request.json or {}
    az_range = int(p["az_end"]) - int(p["az_start"])
    el_range = int(p["el_end"]) - int(p["el_start"])
    resolution = int(p.get("resolution", 1))

    if resolution == 2:
        az_range *= 5
        el_range *= 3

    time_est = az_range * el_range
    minutes = round((time_est + (time_est / 6)) / 60, 2)
    return jsonify({"minutes": minutes})


def _run_scan_locked(*args):
    try:
        _run_scan(*args)
    finally:
        dish_io_lock.release()


@app.route("/api/scan/start", methods=["POST"])
def api_scan_start():
    with state_lock:
        dish = state["dish"]
        if not dish or not dish.is_open:
            return jsonify({"ok": False, "error": "Connect to the dish first."}), 400
        if state["scan"]["running"]:
            return jsonify({"ok": False, "error": "A scan is already running."}), 400

        p = request.json or {}
        az_start = max(0, min(360, int(p["az_start"])))
        az_end = max(0, min(360, int(p["az_end"])))
        el_start = max(5, min(70, int(p["el_start"])))
        el_end = max(5, min(70, int(p["el_end"])))
        resolution = int(p.get("resolution", 1))

        if not dish_io_lock.acquire(blocking=False):
            return jsonify({"ok": False, "error": "Dish is busy with another operation."}), 409

        stop_event = threading.Event()
        state["scan_stop"] = stop_event
        state["scan"] = {
            "running": True, "azimuth": az_start, "elevation": el_start,
            "signal": None, "done": 0, "total": 0, "log": [],
            "last_file": None, "error": None,
        }

        thread = threading.Thread(
            target=_run_scan_locked, args=(dish, az_start, az_end, el_start, el_end, resolution, stop_event),
            daemon=True,
        )
        state["scan_thread"] = thread
        thread.start()

    return jsonify({"ok": True})


@app.route("/api/scan/stop", methods=["POST"])
def api_scan_stop():
    _stop_scan()
    return jsonify({"ok": True})


def _stop_scan():
    with state_lock:
        stop_event = state["scan_stop"]
    if stop_event:
        stop_event.set()


@app.route("/api/scan/preview.png")
def api_scan_preview():
    with state_lock:
        png = state["scan_preview_png"]
    if not png:
        return "", 404
    return send_file(io.BytesIO(png), mimetype="image/png")


def _run_scan(dish, az_start, az_end, el_start, el_end, resolution, stop_event):
    timestr = time.strftime("%Y%m%d-%H%M%S")

    try:
        if resolution == 2:
            az_range = (az_end - az_start) * 5
            el_range = (el_end - el_start) * 3
        else:
            az_range = az_end - az_start
            el_range = el_end - el_start

        np.savetxt(f"scan-settings-{timestr}.txt", (az_start, az_end, el_start, el_end, resolution))

        sky_image = Image.new('RGB', [az_range + 1, el_range + 1], 255)
        pixels = sky_image.load()
        sky_data = np.zeros((el_range + 1, az_range + 1))

        log_scan("Moving dish to starting position...")
        _safe_move(dish, 'az', az_start, cancel_event=stop_event)
        _safe_move(dish, 'el', el_start, cancel_event=stop_event)

        state["scan"]["total"] = (el_range) * (az_range)

        if resolution == 1:
            ascending = True
            for elevation in range(el_start, el_end):
                if stop_event.is_set():
                    break
                if elevation != el_start:
                    send_command(dish, f"elangle {elevation}")
                    time.sleep(2)

                if ascending:
                    az_sequence = range(az_start, az_end)
                    row_end_az = az_end - 1
                else:
                    az_sequence = range(az_end - 1, az_start - 1, -1)
                    row_end_az = az_start

                for i, azimuth in enumerate(az_sequence):
                    if stop_event.is_set():
                        break
                    send_command(dish, f"azangle {azimuth}")
                    signal_strength = read_signal_strength(dish, stop_event)
                    if signal_strength is None:
                        break
                    _record_point(sky_data, pixels, sky_image, timestr,
                                   abs(elevation - el_end), abs(azimuth - az_end), signal_strength,
                                   azimuth, elevation)
                    if i == 0 and elevation != el_start:
                        # First step after a direction reversal - gear lash on
                        # a worn drivetrain is most likely to show up right
                        # here, before the row-end check ever runs.
                        _verify_position(dish, 'az', azimuth, cancel_event=stop_event)

                if stop_event.is_set():
                    break

                # Direction is about to reverse - this is where gear lash on a
                # worn drivetrain is most likely to introduce slip, so verify
                # instead of trusting the last sweep command landed cleanly.
                _verify_position(dish, 'az', row_end_az, cancel_event=stop_event)
                ascending = not ascending
        else:
            for elevation in range(0, el_range):
                if stop_event.is_set():
                    break
                for azimuth in range(0, az_range):
                    if stop_event.is_set():
                        break
                    send_command(dish, "aznudge ccw")
                    signal_strength = read_signal_strength(dish, stop_event)
                    if signal_strength is None:
                        break
                    _record_point(sky_data, pixels, sky_image, timestr,
                                   abs(elevation - el_range), abs(azimuth - az_range), signal_strength,
                                   azimuth, elevation)

                if stop_event.is_set():
                    break

                # aznudge is relative, so the tracked az position (set by
                # _safe_move) no longer reflects reality once the nudge loop
                # above has run - without invalidating it first, _safe_move
                # would see "already at az_start" from a prior row's return
                # and skip sending any command at all.
                _invalidate_position('az')
                _safe_move(dish, 'az', az_start, cancel_event=stop_event)
                send_command(dish, "elnudge up")
                _invalidate_position('el')

        state["scan"]["last_file"] = f"raw-data-{timestr}.txt"
        log_scan("Scan complete!" if not stop_event.is_set() else "Scan stopped.")

    except Exception as e:
        state["scan"]["error"] = str(e)
        log_scan(f"Error: {e}")
    finally:
        state["scan"]["running"] = False
        # The sweep used untracked relative/per-degree moves throughout, so
        # the next operation (another scan, a manual move, tracking) should
        # not trust whatever the cache happens to hold - force it to verify.
        _invalidate_position('az')
        _invalidate_position('el')


def _record_point(sky_data, pixels, sky_image, timestr, row, col, signal_strength, azimuth, elevation):
    sky_data[row, col] = signal_strength
    np.savetxt(f"raw-data-{timestr}.txt", sky_data)
    pixels[col, row] = (signal_strength % 255, 0, 0)
    sky_image.save(f"result-{timestr}.png")

    buf = io.BytesIO()
    sky_image.save(buf, format="PNG")
    with state_lock:
        state["scan_preview_png"] = buf.getvalue()
        state["scan"]["azimuth"] = azimuth
        state["scan"]["elevation"] = elevation
        state["scan"]["signal"] = signal_strength
        state["scan"]["done"] += 1
    log_scan(f"Az: {azimuth}  El: {elevation}  Signal: {signal_strength}")


# ────────────────────────────────────────────────────────────────
# CALIBRATE (Ku-band pointing check against known DISH DBS slots)
# ────────────────────────────────────────────────────────────────

@app.route("/api/calibrate/targets")
def api_calibrate_targets():
    with state_lock:
        observer = dict(state["observer"])
    targets = []
    for label, lon in dish_calibrate.CALIBRATION_TARGETS:
        az, el = dish_calibrate.geostationary_az_el(observer["lat"], observer["lon"], lon)
        targets.append({
            "label": label,
            "lon": lon,
            "az": az,
            "el": el,
            "visible": dish_motion.MIN_ELEVATION <= el <= dish_motion.MAX_ELEVATION,
        })
    return jsonify({"targets": targets})


def _run_calibration_locked(*args):
    try:
        _run_calibration(*args)
    finally:
        dish_io_lock.release()


@app.route("/api/calibrate/start", methods=["POST"])
def api_calibrate_start():
    with state_lock:
        dish = state["dish"]
        if not dish or not dish.is_open:
            return jsonify({"ok": False, "error": "Connect to the dish first."}), 400
        if state["calibrate"]["running"]:
            return jsonify({"ok": False, "error": "A calibration run is already in progress."}), 400

        p = request.json or {}
        margin = max(1.0, min(20.0, float(p.get("margin", dish_calibrate.DEFAULT_SEARCH_MARGIN))))
        step = max(0.5, min(10.0, float(p.get("step", dish_calibrate.DEFAULT_SEARCH_STEP))))

        wanted_lons = p.get("targets")
        if wanted_lons:
            # The frontend sends each checkbox's exact signed longitude (as
            # listed in CALIBRATION_TARGETS, e.g. -77.0) - unlike the CLI
            # script's --targets flag, which takes bare magnitudes like
            # "110,61.5" and compares on abs(lon) instead.
            wanted = {float(x) for x in wanted_lons}
            targets = [(label, lon) for label, lon in dish_calibrate.CALIBRATION_TARGETS
                       if any(abs(lon - w) < 0.01 for w in wanted)]
        else:
            targets = list(dish_calibrate.CALIBRATION_TARGETS)
        if not targets:
            return jsonify({"ok": False, "error": "No matching targets."}), 400

        if not dish_io_lock.acquire(blocking=False):
            return jsonify({"ok": False, "error": "Dish is busy with another operation."}), 409

        stop_event = threading.Event()
        state["calibrate_stop"] = stop_event
        state["calibrate"] = {
            "running": True, "current_target": None,
            "azimuth": None, "elevation": None, "signal": None,
            "done": 0, "total": 0, "log": [], "results": [],
            "last_file": None, "error": None,
        }

        thread = threading.Thread(
            target=_run_calibration_locked, args=(dish, targets, margin, step, stop_event),
            daemon=True,
        )
        state["calibrate_thread"] = thread
        thread.start()

    return jsonify({"ok": True})


@app.route("/api/calibrate/stop", methods=["POST"])
def api_calibrate_stop():
    _stop_calibrate()
    return jsonify({"ok": True})


def _stop_calibrate():
    with state_lock:
        stop_event = state["calibrate_stop"]
    if stop_event:
        stop_event.set()


@app.route("/api/calibrate/log")
def api_calibrate_log():
    with state_lock:
        return jsonify({"log": list(state["calibrate"]["log"])})


def _run_calibration(dish, targets, margin, step, stop_event):
    with state_lock:
        observer = dict(state["observer"])
        correction = dict(state["correction"])

    steps_per_axis = round(2 * margin / step) + 1
    with state_lock:
        state["calibrate"]["total"] = len(targets) * steps_per_axis * steps_per_axis

    results = []
    try:
        for label, sat_lon in targets:
            if stop_event.is_set():
                break

            with state_lock:
                state["calibrate"]["current_target"] = label

            az_true, el_true = dish_calibrate.geostationary_az_el(observer["lat"], observer["lon"], sat_lon)
            log_calibrate(f"[{label}] theoretical: compass az {az_true:.1f}, el {el_true:.1f}")

            if not (dish_motion.MIN_ELEVATION <= el_true <= dish_motion.MAX_ELEVATION):
                log_calibrate(f"[{label}] computed elevation {el_true:.1f} is outside the dish's "
                               f"{dish_motion.MIN_ELEVATION}-{dish_motion.MAX_ELEVATION} range - skipping.")
                results.append({"label": label, "found": False})
                continue

            az_dish = dish_track.dish_az_from_compass(az_true)

            # The known pointing-correction offsets (from the Calibrate tab,
            # typically filled in from a previous run's average error) shift
            # where this run actually aims and centers its search - az_true/
            # el_true/az_dish stay as the uncorrected ground truth used for
            # error reporting below, so the reported error becomes the
            # *residual* error remaining after correction, not the raw one.
            az_target = (az_dish + correction["az"]) % 360
            el_target = el_true + correction["el"]
            if correction["az"] or correction["el"]:
                log_calibrate(f"[{label}] applying correction (az {correction['az']:+.1f}, "
                               f"el {correction['el']:+.1f}): aiming at az {az_target:.1f}, el {el_target:.1f}")

            _safe_move(dish, 'az', az_target, cancel_event=stop_event)
            _safe_move(dish, 'el', el_target, cancel_event=stop_event)

            best_az_dish, best_el, best_signal = None, None, -1
            el_lo = max(dish_motion.MIN_ELEVATION, el_target - margin)
            el_hi = min(dish_motion.MAX_ELEVATION, el_target + margin)
            forward = True

            for el in dish_calibrate.frange(el_lo, el_hi, step):
                if stop_event.is_set():
                    break
                _safe_move(dish, 'el', el, cancel_event=stop_event)

                az_values = dish_calibrate.frange(az_target - margin, az_target + margin, step)
                if not forward:
                    az_values = list(reversed(az_values))
                forward = not forward

                for az in az_values:
                    if stop_event.is_set():
                        break
                    az = az % 360
                    _safe_move(dish, 'az', az, cancel_event=stop_event)
                    signal = read_signal_strength(dish, stop_event)

                    with state_lock:
                        state["calibrate"]["azimuth"] = az
                        state["calibrate"]["elevation"] = el
                        state["calibrate"]["signal"] = signal
                        state["calibrate"]["done"] += 1
                    log_calibrate(f"  az {az:6.1f}  el {el:5.1f}  signal: {signal if signal is not None else '(no reply)'}")

                    if signal is not None and signal > best_signal:
                        best_az_dish, best_el, best_signal = az, el, signal

            if stop_event.is_set():
                break

            if best_signal is None or best_signal <= 0:
                log_calibrate(f"[{label}] no signal found in the search window - "
                               f"not visible from here, or the window missed it.")
                results.append({"label": label, "found": False})
                continue

            _safe_move(dish, 'az', best_az_dish, cancel_event=stop_event)  # park on the peak
            _safe_move(dish, 'el', best_el, cancel_event=stop_event)

            best_az_true = dish_track.dish_az_from_compass(best_az_dish)  # self-inverse conversion
            az_error = dish_calibrate.signed_angle_diff(best_az_true, az_true)
            el_error = best_el - el_true
            log_calibrate(f"[{label}] peak signal {best_signal} at compass az {best_az_true:.1f}, "
                           f"el {best_el:.1f}  (error: az {az_error:+.1f}, el {el_error:+.1f})")

            results.append({
                "label": label, "found": True,
                "theoretical_az": az_true, "theoretical_el": el_true,
                "found_az": best_az_true, "found_el": best_el,
                "az_error": az_error, "el_error": el_error,
                "peak_signal": best_signal,
            })

        log_calibrate("Calibration complete!" if not stop_event.is_set() else "Calibration stopped.")
    except Exception as e:
        with state_lock:
            state["calibrate"]["error"] = str(e)
        log_calibrate(f"Error: {e}")
    finally:
        timestr = time.strftime("%Y%m%d-%H%M%S")
        out_file = f"calibration-{timestr}.json"
        with open(out_file, "w") as f:
            json.dump({
                "observer": observer, "correction": correction,
                "margin": margin, "step": step, "results": results,
            }, f, indent=2)

        with state_lock:
            state["calibrate"]["running"] = False
            state["calibrate"]["results"] = results
            state["calibrate"]["last_file"] = out_file
        # The raster search drove both axes with untracked small steps
        # throughout - force the next operation to re-verify rather than
        # trusting whatever position the cache happens to hold.
        _invalidate_position('az')
        _invalidate_position('el')


# ────────────────────────────────────────────────────────────────
# VIEW SAVED SCANS
# ────────────────────────────────────────────────────────────────

@app.route("/api/scan/log")
def api_scan_log():
    with state_lock:
        return jsonify({"log": list(state["scan"]["log"])})


@app.route("/api/files")
def api_files():
    files = sorted(glob.glob("raw-data-*.txt"), key=os.path.getmtime, reverse=True)
    return jsonify({"files": files})


@app.route("/api/image.png")
def api_image():
    filename = request.args.get("file")
    factor = int(request.args.get("factor", dish_image.INTERPOLATION_FACTOR))
    if not filename or not os.path.basename(filename) == filename:
        return jsonify({"error": "invalid filename"}), 400

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    try:
        data, x, az_ticks, y, el_ticks, timestamp = dish_image.load_scan(filename, factor)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"[image] {filename}: {e}", file=sys.stderr)
        return jsonify({"error": "Could not load that scan file."}), 400

    fig = Figure(figsize=(8, 6))
    ax = fig.add_subplot(111)
    im = ax.imshow(data, cmap='inferno', origin='lower', aspect='auto')
    fig.colorbar(im, ax=ax, location='bottom', label='RF Signal Strength')
    ax.set_xticks(x, az_ticks)
    ax.set_yticks(y, el_ticks)
    ax.set_xlabel("Azimuth (dish uses CCW heading)")
    ax.set_ylabel("Elevation")
    ax.set_title("Ku Band Scan " + timestamp)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ────────────────────────────────────────────────────────────────
# OBSERVER POSITION
# ────────────────────────────────────────────────────────────────

@app.route("/api/observer", methods=["GET", "POST"])
def api_observer():
    if request.method == "POST":
        p = request.json or {}
        try:
            lat = max(-90.0, min(90.0, float(p["lat"])))
            lon = max(-180.0, min(180.0, float(p["lon"])))
        except (KeyError, ValueError, TypeError):
            return jsonify({"ok": False, "error": "lat/lon required"}), 400
        with state_lock:
            state["observer"] = {"lat": lat, "lon": lon}
        _save_observer(lat, lon)
    with state_lock:
        return jsonify({"ok": True, **state["observer"]})


@app.route("/api/correction", methods=["GET", "POST"])
def api_correction():
    if request.method == "POST":
        p = request.json or {}
        try:
            az = max(-180.0, min(180.0, float(p["az"])))
            el = max(-70.0, min(70.0, float(p["el"])))
        except (KeyError, ValueError, TypeError):
            return jsonify({"ok": False, "error": "az/el required"}), 400
        with state_lock:
            state["correction"] = {"az": az, "el": el}
        _save_correction(az, el)
    with state_lock:
        return jsonify({"ok": True, **state["correction"]})


# ────────────────────────────────────────────────────────────────
# TRACK
# ────────────────────────────────────────────────────────────────

@app.route("/api/track/sources")
def api_track_sources():
    sources = list(dish_track.TLE_SOURCES.keys())
    # Only offered once credentials actually exist, so the dropdown never
    # shows an option that's guaranteed to fail.
    if _load_space_track_credentials() is not None:
        sources.extend(dish_track.SPACE_TRACK_QUERIES.keys())
    return jsonify({"sources": sources})


def _ranked_response(sats, allow_constellations=False):
    with state_lock:
        observer = state["observer"]
        min_elevation = state["track_settings"]["min_elevation"]
        show_constellations = state["track_settings"]["show_constellations"]

    if not (allow_constellations or show_constellations):
        sats = [s for s in sats if not dish_track.is_constellation_sat(s[0])]

    ranked, was_ranked = dish_track.rank_by_next_pass(sats, observer["lat"], observer["lon"], horizon_deg=min_elevation)
    with state_lock:
        state["satellites"] = [(r["name"], r["l1"], r["l2"]) for r in ranked]
    satellites = [{"name": r["name"], "seconds_until": r["seconds_until"]} for r in ranked]
    return satellites, was_ranked


@app.route("/api/track/settings", methods=["GET", "POST"])
def api_track_settings():
    if request.method == "POST":
        p = request.json or {}
        try:
            min_elevation = max(0.0, min(dish_motion.MAX_ELEVATION, float(p["min_elevation"])))
        except (KeyError, ValueError, TypeError):
            return jsonify({"ok": False, "error": "min_elevation required"}), 400

        max_distance_km = p.get("max_distance_km")
        if max_distance_km in (None, "", 0):
            max_distance_km = None
        else:
            try:
                max_distance_km = max(100.0, min(50000.0, float(max_distance_km)))
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "invalid max_distance_km"}), 400

        show_constellations = bool(p.get("show_constellations", False))

        with state_lock:
            state["track_settings"] = {
                "min_elevation": min_elevation,
                "max_distance_km": max_distance_km,
                "show_constellations": show_constellations,
            }
        _save_track_settings(min_elevation, max_distance_km, show_constellations)

    with state_lock:
        return jsonify({"ok": True, **state["track_settings"]})


@app.route("/api/track/load", methods=["POST"])
def api_track_load():
    source = (request.json or {}).get("source", list(dish_track.TLE_SOURCES.keys())[0])
    is_space_track = source in dish_track.SPACE_TRACK_QUERIES
    if not is_space_track and source not in dish_track.TLE_SOURCES:
        return jsonify({"ok": False, "error": "Unknown source."}), 400

    try:
        if is_space_track:
            creds = _load_space_track_credentials()
            if creds is None:
                raise RuntimeError(f"{SPACE_TRACK_CREDENTIALS_FILE} is missing or incomplete.")
            used_source = source
            sats = dish_track.fetch_space_track_catalogue(*creds, dish_track.SPACE_TRACK_QUERIES[source])
        else:
            # Space-Track needs its own credentialed login, so it isn't
            # part of this automatic Celestrak/AMSAT fallback chain -
            # only tried when explicitly selected, above.
            used_source, sats = dish_track.fetch_tle_catalogue_with_fallback(source)
        fetched_at = time.time()
        _save_tle_cache(used_source, sats, fetched_at)
        cached_fallback = False
    except Exception as e:
        # Every live option failed (e.g. no internet at all right now) -
        # fall back to whatever was last successfully pulled rather than
        # leaving the user with nothing.
        cached = _load_tle_cache()
        if cached is None:
            print(f"[track/load] {source}: {e}", file=sys.stderr)
            return jsonify({"ok": False, "error": "Could not load satellite data and no local cache is available."}), 400
        used_source, fetched_at, sats = cached
        cached_fallback = True

    allow_constellations = used_source in DEDICATED_CONSTELLATION_SOURCES

    with state_lock:
        state["track"]["tle_fetched_at"] = fetched_at
        state["track"]["tle_source"] = used_source
        state["all_satellites"] = sats
    satellites, ranked = _ranked_response(sats, allow_constellations=allow_constellations)
    return jsonify({
        "ok": True,
        # Always a real TLE_SOURCES/SPACE_TRACK_QUERIES key - the frontend
        # assigns this straight to the source <select>'s value, and an
        # annotated string like "X (cached - ...)" would silently clear
        # that dropdown instead of selecting X.
        "source": used_source,
        "cached_fallback": cached_fallback,
        "satellites": satellites,
        "ranked": ranked,
    })


def _find_satellite_by_name(name):
    """Resolves a satellite by name against the *current* satellites list,
    rather than trusting a list index/position from the client - an
    auto-resort (every 30s, or on a "show constellations" toggle) can
    reorder that list between when the browser rendered it and when a
    request referencing a position in it arrives, which would otherwise
    silently select a different satellite than the one actually picked."""
    with state_lock:
        for entry in state["satellites"]:
            if entry[0] == name:
                return entry
    return None


@app.route("/api/track/live")
def api_track_live():
    name = request.args.get("name", "")
    entry = _find_satellite_by_name(name)
    if entry is None:
        return jsonify({"ok": False, "error": "Invalid satellite selection."}), 400
    name, l1, l2 = entry
    with state_lock:
        observer = state["observer"]

    tracker = dish_track.SatelliteTracker(name, l1, l2, observer["lat"], observer["lon"])
    return jsonify({
        "ok": True,
        "live": tracker.get_full_position(),
        "path": dish_track.ground_track(name, l1, l2),
    })


@app.route("/api/track/resort", methods=["POST"])
def api_track_resort():
    """Re-rank the already-loaded satellites against the current observer
    position/time, without re-downloading TLEs. Reads from all_satellites
    (not satellites) so that toggling "show constellations" takes effect
    immediately on the next resort instead of only on the next fresh load."""
    with state_lock:
        sats = list(state["all_satellites"])
        current_source = state["track"]["tle_source"]
    if not sats:
        return jsonify({"ok": False, "error": "No satellites loaded."}), 400
    allow_constellations = current_source in DEDICATED_CONSTELLATION_SOURCES
    satellites, ranked = _ranked_response(sats, allow_constellations=allow_constellations)
    return jsonify({"ok": True, "satellites": satellites, "ranked": ranked})


def _track_callback(message, below_horizon):
    # Deliberately lock-free - called frequently from the tracking thread,
    # and these two fields don't need to be consistent with anything else.
    state["track"]["message"] = message
    state["track"]["below_horizon"] = below_horizon


@app.route("/api/track/start", methods=["POST"])
def api_track_start():
    requested_name = (request.json or {}).get("name", "")
    entry = _find_satellite_by_name(requested_name)
    if entry is None:
        return jsonify({"ok": False, "error": "Invalid satellite selection."}), 400
    name, l1, l2 = entry

    with state_lock:
        dish = state["dish"]
        if not dish or not dish.is_open:
            return jsonify({"ok": False, "error": "Connect to the dish first."}), 400
        step_size = state["motion"]["step_size"]
        tolerance = state["motion"]["tolerance"]
        observer = state["observer"]
        track_settings = state["track_settings"]

    # Stop/join any previous tracker *before* trying to acquire the dish -
    # this must happen without holding state_lock, since the old thread's
    # own exit handler needs that lock too (see _on_track_thread_exit).
    _stop_track()

    if not dish_io_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Dish is busy with another operation."}), 409

    tracker = dish_track.SatelliteTracker(name, l1, l2, observer["lat"], observer["lon"])
    thread = dish_track.TrackingThread(
        tracker, dish, _track_callback,
        step_size=step_size, tolerance=tolerance, on_warning=_record_motion_warning,
        min_elevation=track_settings["min_elevation"],
        max_distance_km=track_settings["max_distance_km"],
        on_position=_record_track_position,
    )
    # on_exit fires from inside the thread itself whenever run() ends, for
    # ANY reason - an external stop, or the thread dying on its own (e.g. a
    # SerialException) - so the dish reservation is always released even if
    # nobody called stop/join on it.
    thread.on_exit = lambda: _on_track_thread_exit(thread)

    with state_lock:
        state["track_thread"] = thread
        state["track"]["running"] = True
        thread.start()

    return jsonify({"ok": True})


@app.route("/api/track/stop", methods=["POST"])
def api_track_stop():
    _stop_track()
    return jsonify({"ok": True})


def _release_track_io_lock_once(thread):
    """Idempotent: release dish_io_lock for this tracking thread exactly
    once, however many code paths notice it has ended."""
    with state_lock:
        if thread is None or getattr(thread, '_io_lock_released', False):
            return
        thread._io_lock_released = True
    dish_io_lock.release()


def _on_track_thread_exit(thread):
    """Called from the TrackingThread itself when its run() loop ends for
    any reason. Only clears the shared slot if this thread is still the
    one registered there (a newer tracker may have already replaced it)."""
    _release_track_io_lock_once(thread)
    with state_lock:
        if state.get("track_thread") is thread:
            state["track_thread"] = None
            state["track"]["running"] = False


def _stop_track():
    with state_lock:
        thread = state["track_thread"]
    if not thread:
        return
    thread.stop()
    # Wait for it to actually exit before anything else (a new track, a
    # scan, a manual move) can touch the same serial port.
    thread.join(timeout=10)
    _release_track_io_lock_once(thread)
    with state_lock:
        if state.get("track_thread") is thread:
            state["track_thread"] = None
            state["track"]["running"] = False


# ────────────────────────────────────────────────────────────────
# PAGE
# ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    # A reboot re-execs this same process right after answering the HTTP
    # request that triggered it - that request's own connection on port
    # 5000 can still be closing in the kernel at that instant, so the
    # first bind() here can transiently fail with "Address already in
    # use" even though nothing is actually still listening. Retry instead
    # of dying on it.
    for attempt in range(20):
        try:
            app.run(host="127.0.0.1", port=5000, threaded=True)
            break
        except (OSError, SystemExit):
            # Werkzeug catches the bind failure itself and calls
            # sys.exit(1) rather than letting an OSError propagate, so
            # SystemExit has to be caught here too.
            if attempt == 19:
                raise
            time.sleep(0.5)
