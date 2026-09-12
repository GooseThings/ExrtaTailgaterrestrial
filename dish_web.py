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
import os
import threading
import time

import numpy as np
import regex as re
import serial
from flask import Flask, jsonify, request, send_file, render_template
from PIL import Image

import dish_image
import dish_track

app = Flask(__name__)

BAUD_RATE = 9600

# ────────────────────────────────────────────────────────────────
# SHARED STATE
# ────────────────────────────────────────────────────────────────

state_lock = threading.Lock()

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

    "satellites": [],
    "track_thread": None,
    "track": {
        "running": False,
        "message": "Not tracking",
        "below_horizon": False,
    },
}


def log_scan(message):
    log = state["scan"]["log"]
    log.append(message)
    del log[:-200]  # keep the last 200 lines


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
        return jsonify({
            "connected": bool(dish and dish.is_open),
            "port": state["port"],
            "scan": {k: v for k, v in state["scan"].items() if k != "log"},
            "track": state["track"],
        })


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
            return jsonify({"ok": False, "error": str(e)}), 400
        state["dish"] = dish
        state["port"] = port
    return jsonify({"ok": True})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    _stop_scan()
    _stop_track()
    with state_lock:
        if state["dish"] and state["dish"].is_open:
            state["dish"].close()
        state["dish"] = None
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

        stop_event = threading.Event()
        state["scan_stop"] = stop_event
        state["scan"] = {
            "running": True, "azimuth": az_start, "elevation": el_start,
            "signal": None, "done": 0, "total": 0, "log": [],
            "last_file": None, "error": None,
        }

        thread = threading.Thread(
            target=_run_scan, args=(dish, az_start, az_end, el_start, el_end, resolution, stop_event),
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
        send_command(dish, f"azangle {az_start}")
        time.sleep(10)
        dish.flush()
        dish.reset_output_buffer()
        send_command(dish, f"elangle {el_start}")
        dish.flush()
        dish.reset_output_buffer()
        dish.reset_input_buffer()
        time.sleep(10)

        state["scan"]["total"] = (el_range) * (az_range)

        if resolution == 1:
            for elevation in range(el_start, el_end):
                if stop_event.is_set():
                    break
                for azimuth in range(az_start, az_end):
                    if stop_event.is_set():
                        break
                    send_command(dish, f"azangle {azimuth}")
                    signal_strength = read_signal_strength(dish, stop_event)
                    if signal_strength is None:
                        break
                    _record_point(sky_data, pixels, sky_image, timestr,
                                   abs(elevation - el_end), abs(azimuth - az_end), signal_strength,
                                   azimuth, elevation)
                send_command(dish, f"azangle {az_start}")
                wait_time = int((az_range) * 0.05) + 1
                time.sleep(wait_time)
                send_command(dish, f"elangle {elevation}")
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
                send_command(dish, f"azangle {az_start}")
                wait_time = int((az_range / 5) * 0.05) + 1
                time.sleep(wait_time)
                send_command(dish, "elnudge up")

        state["scan"]["last_file"] = f"raw-data-{timestr}.txt"
        log_scan("Scan complete!" if not stop_event.is_set() else "Scan stopped.")

    except Exception as e:
        state["scan"]["error"] = str(e)
        log_scan(f"Error: {e}")
    finally:
        state["scan"]["running"] = False


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
        return jsonify({"error": str(e)}), 400

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
# TRACK
# ────────────────────────────────────────────────────────────────

@app.route("/api/track/sources")
def api_track_sources():
    return jsonify({"sources": list(dish_track.TLE_SOURCES.keys())})


@app.route("/api/track/load", methods=["POST"])
def api_track_load():
    source = (request.json or {}).get("source", list(dish_track.TLE_SOURCES.keys())[0])
    if source not in dish_track.TLE_SOURCES:
        return jsonify({"ok": False, "error": "Unknown source."}), 400
    url = dish_track.TLE_SOURCES[source]
    try:
        sats = dish_track.fetch_tle_catalogue(url)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    with state_lock:
        state["satellites"] = sats
    return jsonify({"ok": True, "satellites": [name for name, _, _ in sats]})


def _track_callback(message, below_horizon):
    with state_lock:
        state["track"]["message"] = message
        state["track"]["below_horizon"] = below_horizon


@app.route("/api/track/start", methods=["POST"])
def api_track_start():
    idx = int((request.json or {}).get("index", -1))
    with state_lock:
        dish = state["dish"]
        if not dish or not dish.is_open:
            return jsonify({"ok": False, "error": "Connect to the dish first."}), 400
        if idx < 0 or idx >= len(state["satellites"]):
            return jsonify({"ok": False, "error": "Invalid satellite selection."}), 400

        _stop_track_locked()

        name, l1, l2 = state["satellites"][idx]
        tracker = dish_track.SatelliteTracker(name, l1, l2, dish_track.OBSERVER_LAT, dish_track.OBSERVER_LON)
        thread = dish_track.TrackingThread(tracker, dish, _track_callback)
        state["track_thread"] = thread
        state["track"]["running"] = True
        thread.start()

    return jsonify({"ok": True})


@app.route("/api/track/stop", methods=["POST"])
def api_track_stop():
    _stop_track()
    return jsonify({"ok": True})


def _stop_track_locked():
    thread = state["track_thread"]
    if thread:
        thread.stop()
    state["track_thread"] = None
    state["track"]["running"] = False


def _stop_track():
    with state_lock:
        _stop_track_locked()


# ────────────────────────────────────────────────────────────────
# PAGE
# ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
