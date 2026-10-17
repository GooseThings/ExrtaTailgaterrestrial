# dish_calibrate.py
# Ku-band pointing calibration helper for the Tailgater dish antenna.
#
# The stock LNB is a DISH Network DBS unit: circularly polarized, fixed to
# 12.2-12.7 GHz. That means the only things it can physically see are
# DISH/EchoStar's own satellites - but those are always on, high power, and
# aimed at CONUS, which makes them good calibration beacons even without a
# subscription: rfwatch just measures carrier presence at the tuner
# front-end, not decoded content.
#
# For each known DISH orbital slot, this script computes the theoretical
# compass az/el from your saved location, points the dish there, then
# raster-searches a small window around that point for where the signal
# actually peaks. The gap between "where the math says it should be" and
# "where the signal actually was" is your pointing error for that target.
# A consistent offset across several targets means a systematic error
# (e.g. the coax jack isn't quite at true North, or azangle's zero-point
# has drifted) - random scatter with no consistent sign instead points at
# gear slip/backlash rather than a fixed calibration error.
#
# Requirements: pip install -r requirements.txt
#
# Usage: python3 dish_calibrate.py
#        python3 dish_calibrate.py --targets 110,61.5 --margin 8 --step 1.5 --yes

import argparse
import json
import math
import sys
import time

import regex as re
import serial

import dish_motion
import dish_track

SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 9600
OBSERVER_FILE = "observer_location.json"

EARTH_RADIUS_KM = 6378.137
GEO_RADIUS_KM = 42164.17

# DISH Network's own fleet - circularly polarized, 12.2-12.7 GHz, exactly
# what the stock LNB is built for. Longitudes are west-negative, matching
# observer_location.json.
CALIBRATION_TARGETS = [
    ("DISH Eastern Arc - 77W", -77.0),
    ("DISH Eastern Arc - 72.7W", -72.7),
    ("DISH Eastern Arc - 61.5W", -61.5),
    ("DISH Western Arc - 110W", -110.0),
    ("DISH Western Arc - 119W", -119.0),
    ("DISH Western Arc - 129W", -129.0),
]

DEFAULT_SEARCH_MARGIN = 6.0   # degrees, +/- around the computed point
DEFAULT_SEARCH_STEP = 2.0     # degrees between raster points
READ_SETTLE = 0.5             # seconds before reading a reply, matches dish_motion._read


def load_observer():
    try:
        with open(OBSERVER_FILE) as f:
            data = json.load(f)
            return float(data["lat"]), float(data["lon"])
    except (OSError, ValueError, KeyError):
        return dish_track.OBSERVER_LAT, dish_track.OBSERVER_LON


def geostationary_az_el(obs_lat, obs_lon, sat_lon):
    """True (compass) azimuth and elevation of a geostationary satellite at
    `sat_lon` (degrees, west negative) as seen from (obs_lat, obs_lon).

    Done via plain ECEF vector geometry rather than the usual closed-form
    az/el formulas, which are easy to get sign/quadrant-wrong - this just
    builds both position vectors, takes the difference, and projects it
    into a local East/North/Up frame at the observer."""
    la, lo, ls = math.radians(obs_lat), math.radians(obs_lon), math.radians(sat_lon)

    obs = (
        EARTH_RADIUS_KM * math.cos(la) * math.cos(lo),
        EARTH_RADIUS_KM * math.cos(la) * math.sin(lo),
        EARTH_RADIUS_KM * math.sin(la),
    )
    sat = (GEO_RADIUS_KM * math.cos(ls), GEO_RADIUS_KM * math.sin(ls), 0.0)
    d = tuple(sat[i] - obs[i] for i in range(3))

    up = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    east = (-math.sin(lo), math.cos(lo), 0.0)
    north = (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la))

    d_up = sum(d[i] * up[i] for i in range(3))
    d_east = sum(d[i] * east[i] for i in range(3))
    d_north = sum(d[i] * north[i] for i in range(3))

    el = math.degrees(math.atan2(d_up, math.hypot(d_east, d_north)))
    az = math.degrees(math.atan2(d_east, d_north)) % 360
    return az, el


def signed_angle_diff(a, b):
    """Signed shortest difference a - b, wrapped to (-180, 180]."""
    return ((a - b + 180) % 360) - 180


def frange(start, stop, step):
    n = max(1, round(abs(stop - start) / step))
    return [start + (stop - start) * i / n for i in range(n + 1)]


# ────────────────────────────────────────────────────────────────
# SERIAL HELPERS (same protocol as dish_web.py/dish_scan.py)
# ────────────────────────────────────────────────────────────────

def send_command(dish, text):
    for ch in text:
        dish.write(ch.encode())
    dish.write(b'\r')


def read_signal_strength(dish, retries=10):
    for _ in range(retries):
        try:
            dish.reset_input_buffer()
            send_command(dish, "rfwatch 1")
            dish.flush()
            dish.reset_output_buffer()
            time.sleep(READ_SETTLE)
            reply = dish.read(207).decode(errors='replace').strip()
            header, *readings = reply.split('[5D')
            output = readings[0]
            output = re.sub(r'\p{C}', '', output)
            output = re.sub(r'[^\d]', '', output).strip()
            return int(output)
        except (serial.SerialException, ValueError, IndexError):
            time.sleep(0.1)
            continue
    return None


class Position:
    """Tracks where we last *commanded* the dish, so stepped_move always has
    a real starting point to step from instead of guessing one."""

    def __init__(self):
        self.az = None
        self.el = None

    def move(self, dish, az=None, el=None):
        if az is not None:
            self.az = dish_motion.stepped_move(
                dish, 'az', self.az if self.az is not None else az, az,
                force=self.az is None,
            )
        if el is not None:
            self.el = dish_motion.stepped_move(
                dish, 'el', self.el if self.el is not None else el, el,
                force=self.el is None,
            )


def search_peak(dish, pos, center_az_dish, center_el, margin, step, log):
    """Raster-scans a small window (dish-frame azimuth, true elevation)
    around the computed point, serpentine by row to cut down on travel,
    and returns (best_az_dish, best_el, best_signal)."""
    el_lo = max(dish_motion.MIN_ELEVATION, center_el - margin)
    el_hi = min(dish_motion.MAX_ELEVATION, center_el + margin)

    best_az, best_el, best_signal = None, None, -1
    forward = True
    for el in frange(el_lo, el_hi, step):
        pos.move(dish, el=el)
        az_values = frange(center_az_dish - margin, center_az_dish + margin, step)
        if not forward:
            az_values = list(reversed(az_values))
        forward = not forward

        for az in az_values:
            az = az % 360
            pos.move(dish, az=az)
            signal = read_signal_strength(dish)
            if signal is None:
                log(f"    az {az:6.1f}  el {el:5.1f}  signal: (no reply)")
                continue
            log(f"    az {az:6.1f}  el {el:5.1f}  signal: {signal}")
            if signal > best_signal:
                best_az, best_el, best_signal = az, el, signal

    return best_az, best_el, best_signal


def print_summary(results):
    found = [r for r in results if r.get("found")]
    print("=" * 64)
    print("CALIBRATION SUMMARY")
    print("=" * 64)
    if not found:
        print("No target found a usable signal - nothing to summarize.")
        return

    for r in found:
        print(f"  {r['label']:28s}  az {r['az_error']:+6.1f}°  el {r['el_error']:+6.1f}°  "
              f"(peak signal {r['peak_signal']})")

    avg_az = sum(r['az_error'] for r in found) / len(found)
    avg_el = sum(r['el_error'] for r in found) / len(found)
    print("-" * 64)
    print(f"  Average error across {len(found)} target(s): az {avg_az:+.1f}°, el {avg_el:+.1f}°")
    print()
    print("A consistent offset in the same direction across multiple targets points at")
    print("a systematic error - e.g. the coax jack isn't quite at true North, or the")
    print("firmware's azangle/elangle zero-point has drifted - and the average above is")
    print("a reasonable correction to apply to future compass-to-dish-angle conversions.")
    print("Scatter with no consistent sign instead points at gear slip/backlash rather")
    print("than a fixed calibration error.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default=SERIAL_PORT)
    parser.add_argument('--targets', default=None,
                         help="Comma-separated satellite longitudes to test (e.g. 110,61.5) - "
                              "default: all of CALIBRATION_TARGETS")
    parser.add_argument('--margin', type=float, default=DEFAULT_SEARCH_MARGIN,
                         help="Search window, +/- degrees around the computed point (default: %(default)s)")
    parser.add_argument('--step', type=float, default=DEFAULT_SEARCH_STEP,
                         help="Raster step size in degrees (default: %(default)s)")
    parser.add_argument('--yes', '-y', action='store_true', help="Skip the confirmation prompt")
    args = parser.parse_args()

    obs_lat, obs_lon = load_observer()
    print(f"Observer: {obs_lat:.4f}, {obs_lon:.4f}")

    targets = CALIBRATION_TARGETS
    if args.targets:
        wanted = {float(t.strip()) for t in args.targets.split(',')}
        targets = [(label, lon) for label, lon in CALIBRATION_TARGETS
                   if any(abs(abs(lon) - w) < 0.01 for w in wanted)]
        if not targets:
            print("No matching targets - check --targets against the longitudes in CALIBRATION_TARGETS.")
            sys.exit(1)

    steps_per_axis = round(2 * args.margin / args.step) + 1
    points_per_target = steps_per_axis * steps_per_axis
    est_minutes = len(targets) * points_per_target * (READ_SETTLE + 0.7) / 60
    print(f"{len(targets)} target(s), ~{points_per_target} points each "
          f"(margin +/-{args.margin}deg, step {args.step}deg)")
    print(f"Estimated time: ~{est_minutes:.1f} minutes\n")

    if not args.yes:
        if not input("Proceed? (y/n): ").strip().lower().startswith('y'):
            print("Aborted.")
            return

    print(f"Opening {args.port}...")
    try:
        dish = serial.Serial(args.port, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        print(f"Could not open {args.port}: {e}")
        sys.exit(1)

    pos = Position()
    results = []
    try:
        for label, sat_lon in targets:
            az_true, el_true = geostationary_az_el(obs_lat, obs_lon, sat_lon)
            print(f"\n[{label}]")
            if not (dish_motion.MIN_ELEVATION <= el_true <= dish_motion.MAX_ELEVATION):
                print(f"  computed elevation {el_true:.1f}deg is outside the dish's "
                      f"{dish_motion.MIN_ELEVATION}-{dish_motion.MAX_ELEVATION}deg range - skipping.")
                results.append({"label": label, "found": False})
                continue

            az_dish = dish_track.dish_az_from_compass(az_true)
            print(f"  theoretical: compass az {az_true:.1f}deg, el {el_true:.1f}deg "
                  f"(dish az {az_dish:.1f}deg)")

            pos.move(dish, az=az_dish, el=el_true)
            best_az_dish, best_el, best_signal = search_peak(
                dish, pos, az_dish, el_true, args.margin, args.step, log=print)

            if best_signal is None or best_signal <= 0:
                print(f"  no signal found anywhere in the search window - this slot may not "
                      f"be visible from here, or the window missed it (try a larger --margin).")
                results.append({"label": label, "found": False})
                continue

            pos.move(dish, az=best_az_dish, el=best_el)  # park on the peak before moving on

            best_az_true = dish_track.dish_az_from_compass(best_az_dish)  # self-inverse conversion
            az_error = signed_angle_diff(best_az_true, az_true)
            el_error = best_el - el_true

            print(f"  peak signal {best_signal} at compass az {best_az_true:.1f}deg, el {best_el:.1f}deg")
            print(f"  error: az {az_error:+.1f}deg, el {el_error:+.1f}deg")

            results.append({
                "label": label, "found": True,
                "theoretical_az": az_true, "theoretical_el": el_true,
                "found_az": best_az_true, "found_el": best_el,
                "az_error": az_error, "el_error": el_error,
                "peak_signal": best_signal,
            })
    except KeyboardInterrupt:
        print("\nInterrupted - closing serial port.")
    finally:
        dish.close()

    print()
    print_summary(results)

    timestr = time.strftime("%Y%m%d-%H%M%S")
    out_file = f"calibration-{timestr}.json"
    with open(out_file, "w") as f:
        json.dump({
            "observer": {"lat": obs_lat, "lon": obs_lon},
            "margin": args.margin, "step": args.step,
            "results": results,
        }, f, indent=2)
    print(f"\nSaved results to {out_file}")


if __name__ == "__main__":
    main()
