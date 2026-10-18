# dish_track.py
# Real-time satellite tracker for the Tailgater dish antenna
# dish_track.py authored by Goose N8GMZ | http://github.com/GooseThings/
#
# Downloads TLE data from Celestrak, lets you pick a satellite from a GUI, then drives the dish motors over serial to follow the satellite in real time.
#
# Requirements: pip install skyfield pyserial requests
#
# Usage: python3 dish_track.py
import io
import json
import math
import os
import serial
import socket
import threading
import time
import tkinter as tk
from datetime import timedelta
from tkinter import ttk, messagebox
from urllib.parse import urlparse, parse_qs
import requests

import dish_motion
from sgp4 import exporter, omm
from sgp4.api import Satrec
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

# Satellite sources, all returned by fetch_tle_catalogue() as
# (name, line1, line2) triples. Celestrak is fetched as CSV (OMM), since
# objects cataloged after 2026-07-11 have 6-digit numbers and no TLE-format
# data there; those are converted to Alpha-5 TLE lines locally. Celestrak
# fetches follow its usage policy (https://celestrak.org/usage-policy.php):
# see fetch_celestrak(). AMSAT is plain TLE text on a different host, and
# is the one fallback for a Celestrak source that can't be fetched.
AMSAT_SOURCE_NAME = "AMSAT - Amateur Satellites"
TLE_SOURCES = {
    "Celestrak - Active Satellites": "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=csv",
    AMSAT_SOURCE_NAME: "https://amsat.org/tle/current/nasabare.txt",
    "Celestrak - Stations": "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=csv",
    "Celestrak - Visual (Brightest)": "https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=csv",
    "Celestrak - Weather": "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=csv",
    "Celestrak - GPS Operational": "https://celestrak.org/NORAD/elements/gp.php?GROUP=gps-ops&FORMAT=csv",
    "Celestrak - Starlink": "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=csv",
}

# Celestrak's GP data updates every 2 hours, and the policy asks for at most
# one download per update - and for clients to stop entirely on any
# non-200 reply rather than keep retrying (which gets an IP firewalled).
CELESTRAK_HOST = "celestrak.org"
CELESTRAK_MIN_INTERVAL = 2 * 3600  # seconds between downloads of one group
CELESTRAK_PAUSE = 2 * 3600         # seconds to stop all Celestrak requests after an error
CELESTRAK_CACHE_DIR = "celestrak_cache"
CELESTRAK_PAUSE_FILE = os.path.join(CELESTRAK_CACHE_DIR, "paused.json")

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

# Identify the app honestly, so data providers can tell who is calling and
# who to contact - never disguise it as a browser to get past a 403, which
# is a provider telling us to stop.
REQUEST_HEADERS = {
    "User-Agent": "ExtraTailgaterrestrial/1.0 "
                  "(+https://github.com/GooseThings/ExrtaTailgaterrestrial)",
}


class CelestrakPaused(requests.RequestException):
    """Celestrak is off-limits right now (an earlier error, per its usage
    policy) and there's no cached copy of the requested group to use."""


class SourceUnreachable(requests.ConnectionError):
    """A data source's server didn't accept a connection."""


def check_reachable(url, timeout=3):
    """'Ping' a source before pulling from it: a bare TCP connect to its
    host and port. Unlike ICMP ping this gets through firewalls that only
    pass web traffic, and it sends no HTTP request, so it never counts
    against a provider's download limits. Raises SourceUnreachable."""
    parts = urlparse(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((parts.hostname, port), timeout=timeout):
            pass
    except OSError as e:
        reason = e.strerror or str(e) or type(e).__name__
        raise SourceUnreachable(f"{parts.hostname} is unreachable ({reason}).") from e


def _fmt_time(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _celestrak_cache_path(url):
    group = parse_qs(urlparse(url).query).get("GROUP", ["unknown"])[0]
    return os.path.join(CELESTRAK_CACHE_DIR, f"{group}.csv")


def _celestrak_pause():
    """(until, reason) if Celestrak requests are paused right now, else None."""
    try:
        with open(CELESTRAK_PAUSE_FILE) as f:
            data = json.load(f)
        if float(data["until"]) > time.time():
            return float(data["until"]), data["reason"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _set_celestrak_pause(reason):
    until = time.time() + CELESTRAK_PAUSE
    os.makedirs(CELESTRAK_CACHE_DIR, exist_ok=True)
    with open(CELESTRAK_PAUSE_FILE, "w") as f:
        json.dump({"until": until, "reason": reason}, f)
    return until, reason


def _parse_omm_csv(text):
    """Celestrak CSV (OMM fields) -> (name, line1, line2) triples. Catalog
    numbers over 99999 come out in Alpha-5 form (100954 -> A0954), which
    skyfield reads back as the full number."""
    sats = []
    for fields in omm.parse_csv(io.StringIO(text)):
        try:
            sat = Satrec()
            omm.initialize(sat, fields)
            l1, l2 = exporter.export_tle(sat)
        except (ValueError, KeyError):
            continue  # malformed row, or a number past Alpha-5's 339999
        sats.append((fields["OBJECT_NAME"].strip(), l1, l2))
    return sats


def fetch_celestrak(url):
    """Fetch one Celestrak group per its usage policy. Returns
    (sats, fetched_at, note), where note is a human-readable explanation
    when a cached copy was used because Celestrak is paused, else None.

    - A group downloaded less than CELESTRAK_MIN_INTERVAL ago is served
      from the local cache without contacting Celestrak.
    - Any non-200 reply (redirects included) or connection failure pauses
      *all* Celestrak requests for CELESTRAK_PAUSE, persisted across
      restarts, instead of retrying or moving on to other groups.
    - While paused, a cached copy of the group (of any age) is used if one
      exists; otherwise CelestrakPaused is raised."""
    cache = _celestrak_cache_path(url)
    cached_at = os.path.getmtime(cache) if os.path.exists(cache) else None

    def from_cache():
        with open(cache) as f:
            return _parse_omm_csv(f.read())

    if cached_at is not None and time.time() - cached_at < CELESTRAK_MIN_INTERVAL:
        return from_cache(), cached_at, None

    note = None
    pause = _celestrak_pause()
    if pause is None:
        try:
            # An unreachable host never received a request, so this doesn't
            # pause Celestrak - the next load retries the check right away.
            check_reachable(url)
        except SourceUnreachable as e:
            note = str(e)
            if cached_at is None:
                raise
    if pause is None and note is None:
        try:
            # (connect_timeout, read_timeout) - a firewalled IP shows up as
            # a dead TCP connect, so fail that fast.
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=(5, 30),
                                allow_redirects=False)
            if resp.status_code != 200:
                pause = _set_celestrak_pause(f"HTTP {resp.status_code}")
        except requests.RequestException as e:
            pause = _set_celestrak_pause(f"connection failed ({type(e).__name__})")

    if pause is not None:
        until, reason = pause
        note = (f"Celestrak requests paused until {_fmt_time(until)} after "
                f"{reason}, per its usage policy.")
        if cached_at is None:
            raise CelestrakPaused(note)
    if note is not None:
        return from_cache(), cached_at, note + f" Using the copy from {_fmt_time(cached_at)}."

    sats = _parse_omm_csv(resp.text)
    if not sats:
        # Same reasoning as the TLE path below - don't let an empty 200
        # replace a good cached copy.
        raise requests.RequestException(f"{url} returned no parseable satellites")
    os.makedirs(CELESTRAK_CACHE_DIR, exist_ok=True)
    with open(cache + ".tmp", "w") as f:
        f.write(resp.text)
    os.replace(cache + ".tmp", cache)
    return sats, time.time(), None


def fetch_tle_catalogue(url):
    """Returns (sats, fetched_at, note) - see fetch_celestrak for note."""
    if urlparse(url).hostname == CELESTRAK_HOST:
        return fetch_celestrak(url)

    check_reachable(url)
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

    if not sats:
        # A 200 with an empty body or an HTML error/maintenance page
        # parses to zero satellites rather than raising - without this,
        # that counts as a successful fetch, skips every other fallback
        # source, and (in dish_web.py) overwrites the last good on-disk
        # cache with nothing.
        raise requests.RequestException(f"{url} returned no parseable satellites")

    return sats, time.time(), None


def fetch_tle_catalogue_with_fallback(preferred_name):
    """Try the preferred source, then AMSAT if that wasn't it. Never falls
    through to other Celestrak groups: when one fails, they all would, and
    retrying them after an error is what Celestrak's usage policy says gets
    an IP firewalled.
    Returns (source_name, sats, fetched_at, notes), where notes is a list
    of human-readable explanations for anything that didn't go as asked.
    Raises a RequestException listing every source's error if all fail."""
    names = [preferred_name]
    if preferred_name != AMSAT_SOURCE_NAME:
        names.append(AMSAT_SOURCE_NAME)

    notes = []
    last_error = None
    for name in names:
        try:
            sats, fetched_at, note = fetch_tle_catalogue(TLE_SOURCES[name])
            if note:
                notes.append(note)
            return name, sats, fetched_at, notes
        except requests.RequestException as e:
            notes.append(f"{name}: {e}")
            last_error = e

    # Every source's reason, not just the last one tried.
    raise requests.RequestException("\n".join(notes)) from last_error


def fetch_space_track_catalogue(username, password, query_url):
    """Space-Track's two plain-text formats disagree on whether a name
    line is included ('tle' is bare 2-line sets, '3le' adds the name) -
    rather than risk silently misparsing one of those, this queries the
    GP class as JSON and reads OBJECT_NAME/TLE_LINE1/TLE_LINE2 directly.
    query_url is one of SPACE_TRACK_QUERIES's values."""
    check_reachable(SPACE_TRACK_LOGIN_URL)
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

    if not sats:
        # Same reasoning as fetch_tle_catalogue: an empty-but-200 response
        # (e.g. the query matched nothing, or Space-Track is degraded)
        # must not look like a successful fetch, or it overwrites the
        # last good on-disk cache with nothing.
        raise requests.RequestException("Space-Track query returned no satellites")

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
        self.observer = wgs84.latlon(lat, lon)
        self.satellite = EarthSatellite(l1, l2, name, self.ts)
        # Parsed rather than sliced from line 1, which holds Alpha-5
        # numbers (A0954) for catalog numbers over 99999.
        self.norad_id = str(self.satellite.model.satnum)
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
                        # stepped_move's return is the *clamped* target
                        # (el is capped to dish_motion's MIN/MAX_ELEVATION,
                        # 5-70 - a satellite can pass overhead well above
                        # that). Recording the raw satellite az/el instead
                        # would save a position the dish was never
                        # actually sent to and can't physically be at,
                        # which the next manual move would then treat as
                        # a real baseline.
                        achieved_az = dish_motion.stepped_move(
                            self.dish, 'az', az_current, dish_az,
                            step_size=self.step_size, tolerance=self.tolerance,
                            on_warning=self.on_warning, force=az_unknown,
                            cancel_event=self.stop_event,
                        )
                        achieved_el = dish_motion.stepped_move(
                            self.dish, 'el', el_current, el,
                            step_size=self.step_size, tolerance=self.tolerance,
                            on_warning=self.on_warning, force=el_unknown,
                            cancel_event=self.stop_event,
                        )
                        self.prev_az = achieved_az
                        self.prev_el = achieved_el
                        if self.on_position:
                            self.on_position(achieved_az, achieved_el)

                    # Numbers are shown in the sat info pane (one source, one
                    # clock); this badge just says what is being tracked.
                    self.callback(f"Tracking {self.tracker.name}", False)

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
            used_source, sats, _, notes = fetch_tle_catalogue_with_fallback(preferred)
        except requests.RequestException as e:
            messagebox.showerror("Download Failed", str(e))
            return
        if notes:
            messagebox.showwarning("Satellite Data", "\n".join(notes))
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
