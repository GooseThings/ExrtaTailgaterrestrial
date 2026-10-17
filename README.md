# Tailgater Microwave Imaging & Satellite Tracking

Turn a portable "Tailgater" satellite dish into a Ku-band RF camera and a real-time satellite
tracker, controlled entirely over USB serial.

**Credit:** originally created by Gabe Emerson / Saveitforparts (2023), who reverse-engineered the
Tailgater's serial protocol and built the original scan + heatmap pipeline —
[watch his original video demo](https://youtu.be/lVOTZxNCgTM). This project builds on that
foundation with real-time satellite tracking, a rebuilt imaging pipeline, and a browser-based
control panel.

## What's in here

| Script | What it does |
|---|---|
| `dish_scan.py` | CLI-driven sky scan — sweeps the dish across an az/el range and records RF signal strength at each point |
| `dish_image.py` | Turns a saved scan into a heatmap image, with optional interpolation for smoother output |
| `dish_track.py` | Real-time satellite tracker — pulls live TLE data and drives the dish to follow a satellite across the sky |
| `dish_web.py` | Browser-based control panel combining scanning, image viewing, satellite tracking with a live map, and location setup in one page |
| `dish_app.py` | The same control panel as `dish_web.py`, opened in a native desktop window instead of a browser tab |

## Hardware you'll need

- A Dish Network "Tailgater" portable satellite antenna with a USB connector on the mainboard
  (tested on a 2014 octagonal-enclosure unit with USB-A; partial success on a 2011 mini-USB unit).
  Related antennas (Wallace, VuQube, King Controls, etc.) may also work but aren't guaranteed —
  open the enclosure and look for the USB port to check.
- An A-to-A USB cable
- 13-18V DC power over the coax "F" connector (13V = vertical LNB polarity, 18V = horizontal) —
  from a set-top box, satellite receiver, inline power injector, or a plain DC adapter wired to
  coax. The USB port alone only carries data, not power.
- A Linux PC (tested from 686-class low-resource machines up through modern distros)

Console firmware varies by unit — the primary test unit runs
`pragelato.h 704 2013-08-09 03:41:27Z rudrava`, which supports the `azangle`/`elangle` commands
used throughout this code. Older firmware (e.g. some 2011 units) lacks `azangle` and needs
`aznudge`/`azim` instead.

## Quick start

```
./install.sh
```

Creates a `.venv`, installs everything in `requirements.txt` into it, and flags it if your user
account isn't in the `dialout` group yet (needed to open `/dev/ttyACM0` on Debian/Ubuntu). Prefer
doing it by hand instead? `pip install -r requirements.txt` works the same way it always did.

Every `python3 <script>.py` command below assumes that venv is active in your shell
(`source .venv/bin/activate`) - without it, `python3` resolves to your system interpreter, which
doesn't have any of this installed. No activation needed? Run `.venv/bin/python3 <script>.py`
directly instead.

1. Connect the dish via USB, and power it via the coax jack.
2. Confirm the connection: `lsusb` should show "Microchip Technology, Inc. CDC RS-232 Emulation
   Demo", and `dmesg | grep tty` will show which port it landed on (usually `/dev/ttyACM0`).
3. Run whichever script fits what you want to do (below), or run `python3 dish_web.py` and do
   everything from one browser tab.

You can also talk to the dish directly: `screen /dev/ttyACM0` (or the equivalent on Windows) opens
its serial console. It starts blank — type `help` for a command list and a `GO>` prompt. The
console doesn't accept backspace, so just hit enter to clear a bad line.

## Coordinate system

The Tailgater uses a 360° **counter-clockwise** system with the coax/F connector as "North"/0°.
Azimuth 90 is 90° CCW from the coax jack, 180 is directly opposite it, and 270 is 90° clockwise
from it — backwards from a standard compass heading. All scripts here account for this internally,
but raw output (images, arrays) is in the dish's own reference frame. A common setup is pointing
the coax jack due North so azimuth increases across the Southern sky.

Valid elevation range is roughly 5-70°; going outside that risks overrunning the motors, so every
script clamps to it. Azimuth moves (scans and tracking alike) take the shorter path through
0°/360° rather than naive numeric stepping, so a move from 350° to 10° is a 20° slew, not 340° the
long way around.

## dish_scan.py — scanning the sky

```
python3 dish_scan.py
```

You'll be prompted for start/end azimuth (0-360, default 90-270), start/end elevation (5-70,
default 5-70), and resolution:

- **Low (1)** — one reading per degree using `azangle`/`elangle`. A default-range scan takes
  ~3.5 hours (the `rfwatch` command has a 1-second minimum).
- **High (2)** — finer readings using `aznudge`/`elnudge` ("nudges" are ~0.2°az / ~0.33°el). Much
  slower and can show banding from inconsistent nudge sizing — best kept to small areas. A
  default-range high-res scan could take 72+ hours and generally isn't worth it.

You'll see a time estimate and a confirmation prompt before it starts. During the scan, a live
low-res preview (`result-<timestamp>.png`) updates as it goes — most image viewers with
auto-refresh (e.g. Gnome Image Viewer) will show progress in real time.

Each scan produces three timestamped files:

- `result-<timestamp>.png` — live low-res preview
- `raw-data-<timestamp>.txt` — the raw signal-strength array
- `scan-settings-<timestamp>.txt` — the az/el range and resolution used

`dish_scan.py` always returns to its starting azimuth between elevation rows rather than sweeping
back and forth — an earlier back-and-forth approach caused gear-meshing issues that distorted
images, so a bit of speed was traded for accuracy.

The web UI's low-res scan (`dish_web.py`) instead sweeps in a serpentine pattern (alternating
direction each row) to cut down on wasted azimuth travel, and re-checks its actual position against
the firmware's own target at each direction reversal — the point where gear lash is most likely to
cause slip — logging a movement warning instead of silently distorting the image if it doesn't land
cleanly.

## dish_image.py — turning a scan into a heatmap

```
python3 dish_image.py raw-data-<timestamp>.txt
```

Loads the matching `scan-settings-<timestamp>.txt` automatically, applies interpolation (using
scipy if installed, otherwise a simpler pixel-repeat fallback) for a smoother image, and opens a
heatmap using the "inferno" colormap. Change the `cmap=` argument in the `plt.imshow(...)` call
near the bottom of the file to try others — "seismic" and "gnuplot2" work well too, and "hsv" can
help pull detail out of noisy scans.

## dish_track.py — real-time satellite tracking

```
python3 dish_track.py
```

A small GUI: pick a TLE source from the dropdown, click **Load**, **Connect** to open the dish's
serial port, select a satellite, then **Track**. The dish will command `azangle`/`elangle` to
follow it across the sky, waiting and auto-starting if the satellite is currently below the
horizon.

Built-in TLE sources:

| Source | Covers |
|---|---|
| Celestrak - Active Satellites | Celestrak's full curated active-satellite catalogue |
| Celestrak - Stations | ISS and other crewed stations |
| Celestrak - Visual (Brightest) | The ~100 brightest visible satellites |
| Celestrak - Weather | Weather satellites |
| Celestrak - GPS Operational | Operational GPS satellites |
| Celestrak - Starlink | Starlink |
| AMSAT - Amateur Satellites | Ham/amateur satellites only |
| Space-Track - Active Satellites | The authoritative source Celestrak itself pulls from, on a different host |
| Space-Track - Starlink | Starlink, from Space-Track |

Celestrak runs its own abuse-protection firewall and can blacklist an IP outright (a silent
connection timeout, not an HTTP error, so it can look like a hang) - if your pick fails, AMSAT
(the one source here on a genuinely different host) is always tried next, before cycling through
any other Celestrak-hosted group. The last successfully-fetched catalogue is cached to disk
(`tle_cache.json`, gitignored) and reloaded automatically on startup, and used as a last-resort
fallback if every live source fails.

The two Space-Track sources need a free account
([sign up here](https://www.space-track.org/auth/createAccount)) - create
`space_track_credentials.json` next to the scripts (gitignored, never committed):

```json
{"username": "you@example.com", "password": "your-password"}
```

Add more sources by editing `TLE_SOURCES` (plain-text feeds) or `SPACE_TRACK_QUERIES` (another
Space-Track query) at the top of `dish_track.py` - any plain-text TLE feed (name/line1/line2
triples) works without further changes, since `fetch_tle_catalogue()` parses that format
generically.

Edit these constants at the top of the file for your setup:

```python
SERIAL_PORT     = '/dev/ttyACM0'  # your dish's port
OBSERVER_LAT    = 42.87           # your latitude
OBSERVER_LON    = -85.68          # your longitude
UPDATE_INTERVAL = 2.0             # seconds between position updates
```

(The web UI below has its own Location tab for this instead of editing constants.)

## dish_web.py — everything in one browser tab

```
python3 dish_web.py
```

Then open http://localhost:5000. One page sharing a single dish connection instead of juggling
multiple scripts, across five tabs:

- **Scan** — the same sky-scan as `dish_scan.py`'s low-res mode, but swept in a serpentine
  pattern (alternating direction each row) to cut down on wasted azimuth travel, with a live
  preview and a movement-warning badge that lights up if the dish doesn't land where commanded
  (gear lash/slip on a worn drivetrain). Results are saved with the same filenames as
  `dish_scan.py`, so they immediately show up in the View tab.
- **View Image** — renders any saved scan (from either this or `dish_scan.py`) as a heatmap.
- **Track** — load any of `dish_track.py`'s TLE sources (see above), with the satellite list
  sorted by how soon each one clears your minimum elevation and auto-refreshing every 30s, plus a
  live map showing the selected satellite's ground position, its projected ~100-minute ground
  track, and a pin for your own location. Settings here let you set a minimum elevation (to clear
  trees/obstructions) and a maximum tracking distance (too-far satellites won't have a usable
  signal, so the dish won't bother slewing to them) - once a tracked pass drops below either, the
  dish stops and reports why (`[BELOW X°]` / `[TOO FAR]`) instead of continuing to chase it. A
  "show internet constellation sats" toggle controls whether Starlink/OneWeb/Kuiper/
  Iridium/Globalstar/Orbcomm show up in the general lists (off by default, since they're not
  usable for amateur radio - picking one of those constellations as the source directly always
  shows it regardless of the toggle).
- **Calibrate** — points the dish at DISH Network's own Ku-band DBS slots (see "Testing/calibrating
  without a subscription" below) and raster-searches a small window around each computed position
  for where the signal actually peaks, reporting the gap between theory and reality as a pointing
  check. A **Quick Calibration** option checks just the single best-elevation visible target with a
  tighter window, for a fast sanity check instead of the full multi-target sweep. Az/el correction
  offsets can be set here too - applied to where future calibration runs aim, so reported error
  becomes the residual left after correction - and persist across restarts in
  `pointing_correction.json` (gitignored).
- **Location** — click or drag a pin on a map to set where the dish is sitting, used for all the
  pass/rise-time/az-el math above. Persisted to `observer_location.json` (gitignored), so it
  survives a restart.

A **Reboot Server** button in the header restarts the `dish_web.py` process itself from the
browser - handy when you need to pick up a settings change without getting back to the machine.

This is a local tool: the dev server only listens on `localhost` and isn't meant to be exposed to
a network. There's no login/auth of any kind, so as defense in depth every state-changing request
is rejected unless its Origin matches this server's own (blocks a drive-by CSRF form from some
other open tab, not a determined attacker with real access to the machine).

### Testing/calibrating without a subscription

The stock LNB is a DISH Network DBS unit - circularly polarized, fixed to 12.2-12.7 GHz - so the
only things it can physically see are DISH/EchoStar's own satellites. Nearly all of that is
encrypted pay content, but `rfwatch` only measures carrier presence at the tuner front-end, not
decoded content, which makes those slots good always-on calibration beacons even with no
subscription at all. That's what the Calibrate tab (and the standalone `dish_calibrate.py`,
runnable without the web UI - see its own `--help`) points at.

### Desktop window (dish_app.py) - not a browser

```
pip install pywebview pyside6 qtpy
python3 dish_app.py
```

`dish_web.py`'s exact same Flask app, run in a background thread and opened in a real OS window
via [pywebview](https://pywebview.flowrl.com/) - a title bar, a taskbar/dock entry, no address bar,
nothing to type. `pywebview` auto-detects whatever's actually installed on the machine it runs on
(WebKitGTK if present, Qt otherwise), which is why both get pulled in above - `pyside6`/`qtpy` are
a pure pip install with no system packages needed, so this works the same way without `sudo` on any
Linux box (and, down the line, on Windows). Already have WebKitGTK on this machine instead (common
on many desktop Linux setups)? `sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1` (the
WebKit package name varies a bit by distro version) gets picked up automatically too, and skips
pulling in Qt/Chromium entirely.

`dish_web.py` itself is unchanged and still runs the old way too (`python3 dish_web.py` + open the
URL in any browser) - still the right choice headless (no desktop at all) or to reach the dish's UI
from another device on the same network, like a phone, while this machine runs the server.

### Installing it as a real app (start menu entry, icon, `apt remove` to undo)

```
./build_deb.sh
sudo apt install ./extratailgaterrestrial_1.0.0_all.deb
```

Builds and installs a `.deb` wrapping `dish_app.py` - afterward it shows up in your applications
menu with an icon (generated by `packaging/generate_icon.py`, since there wasn't an existing
project logo to use), and `extratailgaterrestrial` works as a plain command too. Uninstall the
normal way:

```
sudo apt remove extratailgaterrestrial    # keeps nothing behind
sudo apt purge extratailgaterrestrial     # also drops its venv
```

A few things worth knowing about what this actually does:

- **It needs internet access at install time, and takes a few minutes the first time.** Rather than
  shipping a pre-built venv in the `.deb` (tied to one exact Python build, and a couple hundred MB
  bigger - mostly Qt/WebEngine), its `postinst` script builds one under
  `/opt/extratailgaterrestrial/.venv` and `pip install`s into it right then. `apt` won't show
  progress for this step - it's working, not stuck.
- **Your settings/scans/calibration results go to `~/.local/share/extratailgaterrestrial/`**, not
  `/opt/extratailgaterrestrial` - that directory is root-owned program files managed by `dpkg`, so
  the installed launcher (`packaging/launcher.sh`) `cd`s into a proper per-user state directory
  first (XDG Base Directory convention), same idea as any other installed app. Running
  `python3 dish_app.py` from inside this repo (the previous section) still uses the repo's own
  directory instead, exactly as before - only the installed-via-apt path changed.
- **Still needs `dialout` group membership** to open `/dev/ttyACM0` - the package's `postinst`
  checks for this and tells you the exact command if it's missing, same as `install.sh`.
- `build_deb.sh` takes an optional version argument (`./build_deb.sh 1.1.0`), defaulting to `1.0.0`.

### Standalone binary (no Python install needed, still browser-based)

```
./build_standalone.sh
```

Bundles `dish_web.py` and its `templates`/`static` assets into a single ~50MB executable at
`dist/dish_web` via PyInstaller - copy that one file to another Linux machine and run it directly,
no Python/pip/venv setup required there at all. This is still `dish_web.py`, not `dish_app.py` -
you open a browser and go to `http://localhost:5000` same as running from source, just without
needing Python installed on that machine. It still reads/writes the usual state files
(`observer_location.json`, `track_settings.json`, etc.) relative to whatever directory it's run
from, and still needs the same `dialout` group membership to open the serial port.

Bundling `dish_app.py` itself into a single executable is a real goal too, but a harder one -
PyInstaller freezing a Qt/WebEngine or GTK/WebKit app correctly (locale files, the separate
WebEngine process binary, etc.) is known to need more than `--add-data`, and wasn't attempted here
yet.

Windows isn't built yet either, but is a real goal - two concrete things stand in the way for
`dish_web.py`'s standalone binary specifically: PyInstaller doesn't cross-compile, so an `.exe`
needs this script run on an actual Windows machine; and `dish_web.py`'s server-reboot feature uses
`subprocess.Popen(..., start_new_session=True)`, a POSIX-only option that would need a Windows
equivalent (likely `CREATE_NEW_PROCESS_GROUP`). Serial port handling itself should already be fine
either way - `pyserial` and every port field in this project take a plain string, so a `COM3`-style
port should work without code changes. `dish_app.py` is a nearer-term, better bet for Windows
specifically, since PySide6 ships native Windows wheels already.

## Examples

`examples/` has a full sample scan (raw data + settings + preview) from a South-centered scan
(5°-70° elevation) taken near St. Paul, MN. `images/` has rendered output for reference:

- `dish_image example.png` — a default scan processed into a heatmap, showing geostationary TV
  satellites
- `satellite overlay.png` — the same heatmap overlaid on a panoramic photo of the actual sky
- `satellite preview.png` — a scaled-up version of the live scan preview
- `room_overlay.png` — an indoor scan showing RF leakage from a poorly-shielded PC
- `house.png` — a structure scan comparing Ku band, visible light, and a 50% overlay
- `tailgater.png` — the antenna itself
- `Selection_Detail.png` / `Selection_Low.png` / `Selection_High.png` — the same patch of sky at
  low vs. high resolution, with the scanned area outlined

## Caveats

This is experimental, amateur radio/RF work — not built by an RF engineer — and it will likely
void any warranty on your Tailgater. Expect rough edges. Issues and pull requests are welcome.

## License

Public domain — see [LICENSE](LICENSE).
