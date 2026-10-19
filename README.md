# Tailgater Microwave Imaging & Satellite Tracking

Turns a portable "Tailgater" satellite dish into a Ku-band RF camera and a real-time satellite
tracker, controlled entirely over USB serial. Scan the sky for RF sources, track satellites live
with az/el pointing, and calibrate the dish's pointing accuracy — all from one native desktop app
or a browser.

**Credit:** built on [Gabe Emerson / Saveitforparts's](https://youtu.be/lVOTZxNCgTM) original
Tailgater serial-protocol reverse-engineering and scan pipeline (2023).

![The Track tab: live satellite selection, ground track map, and dish status](images/screenshot.png)

## Hardware you'll need

- A Dish Network "Tailgater" portable antenna with a USB port on the mainboard (tested on a 2014
  octagonal unit; partial support on 2011 mini-USB units)
- An A-to-A USB cable
- 13–18V DC power over the coax F connector (from a receiver, power injector, or DC adapter — the
  USB port alone carries data only)
- A Linux PC

## Install

The recommended way is the project's signed apt repository (Debian/Ubuntu). It gives you a real
desktop app with a start-menu entry and icon, and updates arrive with `sudo apt upgrade`:

```
curl -fsSL https://goosethings.github.io/ExrtaTailgaterrestrial/extratailgaterrestrial.gpg \
  | sudo tee /usr/share/keyrings/extratailgaterrestrial.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/extratailgaterrestrial.gpg] https://goosethings.github.io/ExrtaTailgaterrestrial stable main" \
  | sudo tee /etc/apt/sources.list.d/extratailgaterrestrial.list
sudo apt update
sudo apt install extratailgaterrestrial
```

The signing key's fingerprint is `7542 B1C3 BC68 5BC9 2DB6 7704 43C9 39F1 CAC0 BC63`. Install
needs internet the first time (the package sets up its own Python environment) and takes a few
minutes. Remove it with `sudo apt remove extratailgaterrestrial` (or `apt purge`).

Each [release](https://github.com/GooseThings/ExrtaTailgaterrestrial/releases) also has the
`.deb` attached, or build it yourself with `./install.sh` then `./build_deb.sh`, and install it
with `sudo apt install ./extratailgaterrestrial_<version>_all.deb`. To reinstall a rebuilt
package with the same version number, add `--reinstall`.

Maintainers publish a new version to the apt repository with `./build_deb.sh` then
`./publish_apt_repo.sh` (after bumping `__version__` in `version.py`).

Prefer running from source instead? `./install.sh` sets up a venv; then either:

```
python3 dish_app.py      # native window (needs: pip install pywebview pyside6 qtpy)
python3 dish_web.py      # browser at http://localhost:5000 — also the right choice headless,
                          # or to reach the UI from another device on the same network
```

A prebuilt single-file binary (no Python needed, still browser-based) is available too:
`./build_standalone.sh` → `dist/dish_web`.

Whichever way you install it, you'll likely need your user in the `dialout` group to open the
dish's serial port — each installer checks for this and tells you the exact command if missing.

## Using it

Connect the dish via USB and power it over the coax jack — `lsusb` should show "Microchip
Technology, Inc. CDC RS-232 Emulation Demo", and `dmesg | grep tty` shows which port it landed on
(usually `/dev/ttyACM0`). Then, in the app:

| Tab | What it does |
|---|---|
| **Scan** | Sweeps the dish across an az/el range, recording RF signal strength at each point, with a live heatmap preview. Saved scans can be re-rendered as heatmaps (with optional interpolation) on the same page |
| **Track** | Pulls live TLE data and drives the dish to follow a satellite in real time, with a live ground-track map |
| **Calibrate** | Points at known Ku-band DBS satellites and compares theory vs. measured signal peak to check (and correct) pointing accuracy |
| **Manual** | Point the dish at a typed-in az/el and watch a live signal meter |
| **Location** | Sets where the dish is sitting, used for all tracking/calibration math |

One quirk worth knowing: the Tailgater measures azimuth **counter-clockwise** from the coax jack
("North" = 0°), backwards from a standard compass — the app handles this conversion internally, but
raw saved data is in the dish's own frame. Valid elevation is roughly 5–70°.

The app converts degrees to the board's raw sensor counts itself and moves with the firmware's
closed-loop `azacc`/`elacc` commands (accurate to about ±2 counts). It skips the firmware's own
degree commands, whose azimuth table is broken between 200° and 212°. The board's full command
set, NVRAM layout and quirks are documented in [docs/FIRMWARE.md](docs/FIRMWARE.md).

## Satellite data

The Track tab can load from Celestrak, AMSAT, or Space-Track. Before each download the app checks
the server is reachable, and tells you if it isn't.

Space-Track needs a [free account](https://www.space-track.org/auth/createAccount). Its sources
only appear once you save the login as `space_track_credentials.json`:

```
{"username": "you@example.com", "password": "…"}
```

The installed app (`.deb`) reads it from `~/.local/share/extratailgaterrestrial/`; when running
from source, put it in the directory you run the app from. Keep it private with
`chmod 600 space_track_credentials.json`. If it's missing, the Track tab says where it looked.

Celestrak downloads follow its [usage policy](https://celestrak.org/usage-policy.php):

- Each list is downloaded at most once every 2 hours. Loads in between use a local copy.
- After any error reply, all Celestrak requests pause for 2 hours. The app uses the local copy
  if it has one, otherwise AMSAT, and tells you why.
- Data is fetched as CSV, so satellites numbered 100000 and up are included.

Command-line scripts (`dish_scan.py`, `dish_image.py`, `dish_track.py`) are also available for
scripting/automation outside the app — see the comment header at the top of each file.

## Caveats

Experimental amateur radio/RF work, not built by an RF engineer — expect rough edges. Issues and
PRs welcome.

## License

[Unlicense](LICENSE) — public domain.
