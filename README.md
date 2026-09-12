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
| `dish_web.py` | Browser-based control panel combining scanning, image viewing, and tracking in one page |

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
pip install -r requirements.txt
```

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
script clamps to it.

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

The scan always returns to its starting azimuth between elevation rows rather than sweeping back
and forth — an earlier back-and-forth approach caused gear-meshing issues that distorted images,
so a bit of speed was traded for accuracy.

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

A small GUI: click **Load** to pull the current active-satellite TLE catalogue from Celestrak,
**Connect** to open the dish's serial port, select a satellite, then **Track**. The dish will
command `azangle`/`elangle` to follow it across the sky, waiting and auto-starting if the
satellite is currently below the horizon.

Edit these constants at the top of the file for your setup:

```python
SERIAL_PORT     = '/dev/ttyACM0'  # your dish's port
OBSERVER_LAT    = 42.87           # your latitude
OBSERVER_LON    = -85.68          # your longitude
UPDATE_INTERVAL = 2.0             # seconds between position updates
```

## dish_web.py — everything in one browser tab

```
python3 dish_web.py
```

Then open http://localhost:5000. One page with three tabs — Scan, View Image, Track — sharing a
single dish connection instead of juggling three scripts. Scan results are saved with the same
filenames as `dish_scan.py`, so they immediately show up in the View tab. This is a local tool:
the dev server only listens on `localhost` and isn't meant to be exposed to a network.

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
