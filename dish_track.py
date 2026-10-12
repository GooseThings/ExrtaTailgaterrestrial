# dish_track.py
# Real-time satellite tracker for the Tailgater dish antenna
# dish_track.py authored by Goose N8GMZ | http://github.com/GooseThings/
#
# Downloads TLE data from Celestrak, lets you pick a satellite from a GUI, then drives the dish motors over serial to follow the satellite in real time.
#
# Requirements: pip install skyfield pyserial requests
#
# Usage: python3 dish_track.py
import math
import serial
import threading
import tkinter as tk
from datetime import timedelta
from tkinter import ttk, messagebox
import requests

import dish_motion
from skyfield.api import load, EarthSatellite, wgs84

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 9600

OBSERVER_LAT = 42.87
OBSERVER_LON = -85.68

MIN_ELEVATION = 5
MAX_ELEVATION = 70

UPDATE_INTERVAL = 2.0

# Movement threshold (degrees)
AZ_THRESHOLD = 1.0
EL_THRESHOLD = 1.0

# Plain-text TLE sources (name/line1/line2 triples) — all parsed the same way
# by fetch_tle_catalogue(), so adding another provider is just another entry.
# Celestrak is the biggest catalogue, but it does go down sometimes (or get
# an IP blacklisted by its abuse protection); AMSAT is a solid fallback
# (amateur/ham satellites only, but that's usually exactly what you want to
# track anyway) - and since it's a genuinely different host, it's also
# tried before any of the other Celestrak-hosted groups below, see
# fetch_tle_catalogue_with_fallback().
AMSAT_SOURCE_NAME = "AMSAT - Amateur Satellites"
TLE_SOURCES = {
    "Celestrak - Active Satellites": "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle",
    AMSAT_SOURCE_NAME: "https://amsat.org/tle/current/nasabare.txt",
    "Celestrak - Stations": "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle",
    "Celestrak - Visual (Brightest)": "https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=tle",
    "Celestrak - Weather": "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
    "Celestrak - GPS Operational": "https://celestrak.org/NORAD/elements/gp.php?GROUP=gps-ops&FORMAT=tle",
    "Celestrak - Starlink": "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle",
}

# Space-Track is the authoritative US government source Celestrak itself
# pulls from - a genuinely different host from the Celestrak/AMSAT sources
# above, so it keeps working even while Celestrak is blocked. It needs a
# free account (https://www.space-track.org/auth/createAccount) and isn't
# part of TLE_SOURCES/fetch_tle_catalogue_with_fallback: it needs
# credentials that may not be configured, so it's only ever used when the
# user explicitly picks it (handled in dish_web.py).
SPACE_TRACK_SOURCE_NAME = "Space-Track - Active Satellites"
SPACE_TRACK_STARLINK_SOURCE_NAME = "Space-Track - Starlink"
SPACE_TRACK_LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
_SPACE_TRACK_BASE = "https://www.space-track.org/basicspacedata/query/class/gp/"
_SPACE_TRACK_COMMON = "DECAY_DATE/null-val/EPOCH/%3Enow-30/OBJECT_TYPE/PAYLOAD/orderby/NORAD_CAT_ID/format/json"
# Each entry needs its own login + query, so these aren't part of
# TLE_SOURCES/fetch_tle_catalogue_with_fallback - see fetch_space_track_catalogue.
SPACE_TRACK_QUERIES = {
    SPACE_TRACK_SOURCE_NAME: _SPACE_TRACK_BASE + _SPACE_TRACK_COMMON,
    SPACE_TRACK_STARLINK_SOURCE_NAME: _SPACE_TRACK_BASE + "OBJECT_NAME/~~STARLINK/" + _SPACE_TRACK_COMMON,
}

# Mega-constellation satellites that show up in the broad "active
# satellites" catalogues (Starlink, OneWeb, etc.) but aren't generally
# usable by amateur radio - excluded from the general lists by default
# (see dish_web.py's "show constellations" setting), but never from a
# source picked specifically for one of them (e.g. "Space-Track - Starlink"
# above, or "Celestrak - Starlink").
CONSTELLATION_NAME_PATTERNS = ("STARLINK", "ONEWEB", "KUIPER", "IRIDIUM", "GLOBALSTAR", "ORBCOMM")


def is_constellation_sat(name):
    upper = name.upper()
    return any(p in upper for p in CONSTELLATION_NAME_PATTERNS)

# ─────────────────────────────────────────────────────────────────
# SERIAL HELPERS
# ─────────────────────────────────────────────────────────────────

def send_command(dish, text):
    """Safer command send"""
    cmd = (text + '\r').encode()
    dish.write(cmd)
    dish.flush()


def dish_az_from_compass(compass_az):
    return (360 - compass_az) % 360


# ─────────────────────────────────────────────────────────────────
# TLE
# ─────────────────────────────────────────────────────────────────

# Celestrak's CDN returns 403 to the default python-requests User-Agent,
# so a browser-like one is required or every request gets blocked outright.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def fetch_tle_catalogue(url):
    # (connect_timeout, read_timeout) - Celestrak's failure mode here is a
    # dead TCP connect (SYN never ACKed), not a slow response, so there's
    # no reason to wait as long for that as for an actual data transfer.
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=(5, 15))
    resp.raise_for_status()

    lines = [l.strip() for l in resp.text.splitlines() if l.strip()]
    sats = []

    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i:i+3]
        if l1.startswith('1 ') and l2.startswith('2 '):
            sats.append((name, l1, l2))
            i += 3
        else:
            i += 1

    return sats


def fetch_tle_catalogue_with_fallback(preferred_name):
    """Try the preferred source first, then AMSAT (if that wasn't already
    the preferred one), then whatever's left of TLE_SOURCES. AMSAT goes
    second specifically because it's the only source here on a genuinely
    different host - if Celestrak is down/blocked, the other entries
    above are all Celestrak-hosted too and would just fail the same way,
    wasting a connect-timeout on each before ever reaching AMSAT.
    Returns (source_name, sats). Raises the last error if every source
    fails."""
    names = [preferred_name]
    if preferred_name != AMSAT_SOURCE_NAME:
        names.append(AMSAT_SOURCE_NAME)
    names.extend(n for n in TLE_SOURCES if n not in names)

    last_error = None
    for name in names:
        try:
            sats = fetch_tle_catalogue(TLE_SOURCES[name])
            return name, sats
        except requests.RequestException as e:
            last_error = e

    raise last_error


def fetch_space_track_catalogue(username, password, query_url):
    """Space-Track's two plain-text formats disagree on whether a name
    line is included ('tle' is bare 2-line sets, '3le' adds the name) -
    rather than risk silently misparsing one of those, this queries the
    GP class as JSON and reads OBJECT_NAME/TLE_LINE1/TLE_LINE2 directly.
    query_url is one of SPACE_TRACK_QUERIES's values."""
    session = requests.Session()
    resp = session.post(
        SPACE_TRACK_LOGIN_URL,
        data={"identity": username, "password": password},
        headers=REQUEST_HEADERS, timeout=(5, 15),
    )
    resp.raise_for_status()

    resp = session.get(query_url, headers=REQUEST_HEADERS, timeout=(5, 30))
    resp.raise_for_status()
    records = resp.json()

    sats = []
    for rec in records:
        l1, l2 = rec.get("TLE_LINE1"), rec.get("TLE_LINE2")
        if not l1 or not l2:
            continue
        tle_line0 = rec.get("TLE_LINE0") or ""
        if tle_line0.startswith("0 "):
            tle_line0 = tle_line0[2:]
        name = rec.get("OBJECT_NAME") or tle_line0 or "UNKNOWN"
        sats.append((name, l1, l2))
    return sats


# Ranking is a per-satellite find_events() call (~5ms each) - fine for a
# few hundred satellites, but a catalogue the size of Space-Track's full
# payload list (tens of thousands) turns that into a synchronous
# multi-minute hang on every load *and* breaks the 30s auto-resort
# entirely. Past this many satellites, skip ranking and just alphabetize.
MAX_RANK_COUNT = 1500


def rank_by_next_pass(sats, lat, lon, horizon_deg=None, window_hours=24, max_rank_count=MAX_RANK_COUNT):
    """Sort (name, l1, l2) triples by how soon each satellite next clears
    horizon_deg for an observer at (lat, lon). Returns (ranked, was_ranked).

    ranked is a list of dicts with name/l1/l2/seconds_until, soonest
    first. A satellite already above the horizon gets seconds_until=0;
    one that doesn't rise within window_hours gets seconds_until=None and
    sorts last - as does every entry when was_ranked is False (the list
    was too large to rank, so it's alphabetical instead)."""
    if len(sats) > max_rank_count:
        ordered = sorted(sats, key=lambda s: s[0])
        return (
            [{"name": n, "l1": l1, "l2": l2, "seconds_until": None} for n, l1, l2 in ordered],
            False,
        )

    if horizon_deg is None:
        horizon_deg = MIN_ELEVATION

    ts = load.timescale()
    t0 = ts.now()
    t1 = t0 + timedelta(hours=window_hours)
    observer = wgs84.latlon(lat, lon)

    ranked = []
    for name, l1, l2 in sats:
        seconds_until = None
        try:
            sat = EarthSatellite(l1, l2, name, ts)
            times, events = sat.find_events(observer, t0, t1, altitude_degrees=horizon_deg)
            if len(events) > 0:
                if events[0] != 0:
                    # First event in the window is a culminate/set, not a
                    # rise, which means it's already above the horizon.
                    seconds_until = 0.0
                else:
                    seconds_until = (times[0].tt - t0.tt) * 86400.0
        except Exception:
            pass
        ranked.append({"name": name, "l1": l1, "l2": l2, "seconds_until": seconds_until})

    ranked.sort(key=lambda r: (r["seconds_until"] is None, r["seconds_until"]))
    return ranked, True


# ─────────────────────────────────────────────────────────────────
# TRACKER
# ─────────────────────────────────────────────────────────────────

class SatelliteTracker:
    def __init__(self, name, l1, l2, lat, lon):
        self.ts = load.timescale()
        self.name = name
        self.norad_id = l1[2:7].strip()
        self.observer = wgs84.latlon(lat, lon)
        self.satellite = EarthSatellite(l1, l2, name, self.ts)
        self.diff = self.satellite - self.observer  # precompute

    def get_position(self):
        t = self.ts.now()
        top = self.diff.at(t)
        alt, az, dist = top.altaz()
        return az.degrees, alt.degrees, dist.km

    def get_full_position(self):
        """Everything needed to show this satellite on a live map: where
        it is overhead (az/el/distance from the dish) and where it is on
        the ground (subpoint lat/lon/altitude + orbital speed)."""
        t = self.ts.now()
        topocentric = self.diff.at(t)
        alt, az, dist = topocentric.altaz()

        geocentric = self.satellite.at(t)
        subpoint = wgs84.subpoint(geocentric)
        vx, vy, vz = geocentric.velocity.km_per_s
        speed_km_s = math.sqrt(vx * vx + vy * vy + vz * vz)

        return {
            "name": self.name,
            "norad_id": self.norad_id,
            "az": float(az.degrees),
            "el": float(alt.degrees),
            "distance_km": float(dist.km),
            "lat": float(subpoint.latitude.degrees),
            "lon": float(subpoint.longitude.degrees),
            "alt_km": float(subpoint.elevation.km),
            "speed_km_s": float(speed_km_s),
        }


def ground_track(name, l1, l2, minutes=100, step_seconds=20):
    """Sample this satellite's ground track (ignoring any observer) from
    now through `minutes` ahead, for plotting a projected path on a map.
    Returns a list of [lat, lon] points."""
    ts = load.timescale()
    satellite = EarthSatellite(l1, l2, name, ts)
    t0 = ts.now()
    n_steps = int(minutes * 60 / step_seconds)
    times = ts.tt_jd([t0.tt + i * step_seconds / 86400.0 for i in range(n_steps + 1)])

    subpoint = wgs84.subpoint(satellite.at(times))
    lats = subpoint.latitude.degrees.tolist()
    lons = subpoint.longitude.degrees.tolist()
    return [[lat, lon] for lat, lon in zip(lats, lons)]


# ─────────────────────────────────────────────────────────────────
# THREAD
# ─────────────────────────────────────────────────────────────────

class TrackingThread(threading.Thread):
    def __init__(self, tracker, dish, callback,
                 step_size=None, tolerance=None, on_warning=None, on_exit=None,
                 min_elevation=None, max_distance_km=None, on_position=None):
        super().__init__(daemon=True)
        self.tracker = tracker
        self.dish = dish
        self.callback = callback
        self.on_position = on_position
        self.stop_event = threading.Event()

        self.step_size = step_size or dish_motion.DEFAULT_STEP_SIZE
        self.tolerance = tolerance or dish_motion.DEFAULT_TOLERANCE
        self.on_warning = on_warning
        self.on_exit = on_exit

        # min_elevation gates tracking on local obstructions (trees, etc) -
        # distinct from MAX_ELEVATION/dish_motion's MIN_ELEVATION, which are
        # the dish's own mechanical range. max_distance_km=None means no
        # distance cutoff (every pass above min_elevation gets tracked).
        self.min_elevation = MIN_ELEVATION if min_elevation is None else min_elevation
        self.max_distance_km = max_distance_km

        self.prev_az = None
        self.prev_el = None

    def run(self):
        try:
            self._run()
        finally:
            if self.on_exit:
                self.on_exit()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                az, el, dist = self.tracker.get_position()
                dish_az = dish_az_from_compass(az)

                if el < self.min_elevation:
                    self.callback(f"[BELOW {self.min_elevation:.0f}°] El: {el:.1f}°", True)
                elif self.max_distance_km is not None and dist > self.max_distance_km:
                    self.callback(f"[TOO FAR] Dist: {dist:.0f} km", True)
                else:
                    move = False

                    if self.prev_az is None:
                        move = True
                    else:
                        if dish_motion.az_distance(self.prev_az, dish_az) > AZ_THRESHOLD:
                            move = True
                        if abs(self.prev_el - el) > EL_THRESHOLD:
                            move = True

                    if move:
                        az_unknown = self.prev_az is None
                        el_unknown = self.prev_el is None
                        az_current = dish_az if az_unknown else self.prev_az
                        el_current = el if el_unknown else self.prev_el
                        dish_motion.stepped_move(
                            self.dish, 'az', az_current, dish_az,
                            step_size=self.step_size, tolerance=self.tolerance,
                            on_warning=self.on_warning, force=az_unknown,
                            cancel_event=self.stop_event,
                        )
                        dish_motion.stepped_move(
                            self.dish, 'el', el_current, el,
                            step_size=self.step_size, tolerance=self.tolerance,
                            on_warning=self.on_warning, force=el_unknown,
                            cancel_event=self.stop_event,
                        )
                        self.prev_az = dish_az
                        self.prev_el = el
                        if self.on_position:
                            self.on_position(dish_az, el)

                    self.callback(
                        f"Az: {az:.1f}° | El: {el:.1f}° | Dist: {dist:.0f} km",
                        False
                    )

            except serial.SerialException:
                self.callback("Serial disconnected — stopping", False)
                break
            except Exception as e:
                self.callback(f"Error: {e}", False)

            self.stop_event.wait(UPDATE_INTERVAL)

    def stop(self):
        self.stop_event.set()


# ─────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────

class TrackerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("N8GMZ Tailgater Satellite Tracker")

        self.satellites = []
        self.dish = None
        self.thread = None

        self._build()

    def _build(self):
        self.source_var = tk.StringVar(value=list(TLE_SOURCES.keys())[0])
        source_menu = ttk.Combobox(self, textvariable=self.source_var,
                                    values=list(TLE_SOURCES.keys()), state="readonly")
        source_menu.pack(fill="x")

        self.listbox = tk.Listbox(self)
        self.listbox.pack(fill="both", expand=True)

        tk.Button(self, text="Load", command=self.load).pack()
        tk.Button(self, text="Connect", command=self.connect).pack()
        tk.Button(self, text="Track", command=self.track).pack()
        tk.Button(self, text="Stop", command=self.stop).pack()

        self.status = tk.Label(self, text="Ready")
        self.status.pack()

    def load(self):
        preferred = self.source_var.get()
        try:
            used_source, sats = fetch_tle_catalogue_with_fallback(preferred)
        except requests.RequestException as e:
            messagebox.showerror("Download Failed", str(e))
            return
        self.satellites = sats

        self.listbox.delete(0, tk.END)
        for i, (name, _, _) in enumerate(sats):
            self.listbox.insert(tk.END, f"{i}: {name}")

        if used_source == preferred:
            self.status.config(text=f"Loaded {len(sats)} satellites from {used_source}")
        else:
            self.source_var.set(used_source)
            self.status.config(
                text=f"{preferred} unavailable — fell back to {used_source} "
                     f"({len(sats)} satellites)"
            )

    def connect(self):
        if self.dish and self.dish.is_open:
            self.dish.close()
        try:
            self.dish = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        except serial.SerialException as e:
            self.dish = None
            messagebox.showerror("Serial Error", str(e))
            return
        self.status.config(text="Connected")

    def track(self):
        sel = self.listbox.curselection()
        if not sel:
            return

        idx = sel[0]
        name, l1, l2 = self.satellites[idx]

        tracker = SatelliteTracker(name, l1, l2, OBSERVER_LAT, OBSERVER_LON)

        self.thread = TrackingThread(
            tracker,
            self.dish,
            lambda msg, _: self.after(0, lambda: self.status.config(text=msg))
        )
        self.thread.start()

    def stop(self):
        if self.thread:
            self.thread.stop()
            self.thread = None

    def on_close(self):
        self.stop()
        if self.dish:
            self.dish.close()
        self.destroy()


# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TrackerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
