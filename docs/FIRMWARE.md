# Tailgater Control Board: Firmware Reference

Everything here was read directly off a connected 2014 octagonal Tailgater over `/dev/ttyACM0`
on 2026-10-18, by black-box testing through the USB console. The firmware binary itself was
**not** read (see [Getting the actual code](#getting-the-actual-code)). Items marked
**(inferred)** are guesses from the data, and **(untested)** items were never run. The raw serial
transcript (including an NVRAM backup) is in
[`firmware-capture-20261018.log`](firmware-capture-20261018.log).

## Identity

| | |
|---|---|
| USB ID | `04d8:000a`: Microchip "CDC RS-232 Emulation Demo" (PIC with USB CDC stack) |
| `ver` | `Version: $Id: pragelato.h 704 2013-08-09 03:41:27Z rudrava $` |
| Codename | "pragelato" (SVN rev 704, Aug 2013) |

## Link layer and parser

- 9600 8N1, line ending `\r`. The board echoes input and replies with `\r\n`. The prompt is `GO>`.
- **Send one character per `write()`.** The firmware drops characters from multi-byte writes. A
  ~10 ms gap between characters is reliable.
- Command names are **exact and case-sensitive**: no prefixes (`ve`), no case folding (`VER`), no
  aliases (`?`). Leading spaces, trailing spaces, and extra arguments are ignored.
- Unknown names reply `Unknown command, sorry...`. `help <cmd>` ignores the argument and prints
  the full list. There is no per-command help.
- **⚠ Numbers are parsed atoi-style with no validation.** A non-numeric argument becomes `0`
  and **is executed**. `elev x` printed `target: 0000` and started driving elevation toward count
  0 (below 0°). The only guard is an argument-*count* check, which applies to some commands and
  not others (see the table). Never send a move command without an explicit, in-range number.
- **Negative numbers wrap as unsigned 16-bit.** `elangle -5` gives `Dest Angle: 65531`,
  `Dest Count: 51358`. Nothing is clamped to the mechanical range (`elangle 90` is accepted).
- **Any new move command preempts the one in progress.** This is the way to cancel a move. There
  is no dedicated stop command.
- **Async completion lines:** `elev`/`azim`/`elacc`/`azacc` (with a target) return to `GO>`
  immediately and print `Stopped at El: N` / `Stopped at Az: N` when the motor finishes. That line
  can land anywhere, including in the middle of the echo of your next command
  (`elangleStopped at El: 1454\r\n 0`).
- `rfwatch` redraws its value in place with ANSI `ESC[5D` (cursor left 5).

## Hidden commands

I tried 228 extra names: info/debug words (`debug`, `dump`, `peek`, `mem`, `diag`, `info`,
`status`, …), hardware words (`lnb`, `tuner`, `motor`, `pwm`, `adc`, `i2c`, …), single letters and
symbols, and variants of the real commands (`azimmt`, `nvdump`, `rfacc`, …). **None exist.** The
command table looks like exactly the 20 entries `help` lists. I did not try names that sound
destructive (`erase`, `format`, `flash`, `boot`, `factory`, `park`, …).

## Command set

Arg check: ✅ means missing args print usage, ❌ means missing or bad args execute as 0.

### Absolute moves: raw sensor counts

| Command | Arg check | Behavior (verified) |
|---|---|---|
| `elev` | n/a | `Current elevation: 1455` |
| `elev <count>` | ❌ | Move to count. `target: N    current: N`, then async `Stopped at El:`. Overshoot or undershoot is typically ±10 counts (stopped at 1450 when aimed at 1460). |
| `elacc <count>` | ❌ | **Accurate** elevation move to count. Landed within ±2 (1460→1462, 1455→1455). **Use this.** |
| `elevmt <count>` | ❌ | "Move and maintain". Acks `target/current` but prints no `Stopped at`. Did **not** correct a −19 count disturbance made with `elnudge` (nudges may cancel it). Behavior inconclusive. |
| `azim` | n/a | `Current heading:       4341`. Jitters ±3–4 counts at rest. |
| `azim <count>` | ❌ | Move to count, async `Stopped at Az:`. |
| `azacc <count>` | ❌ | **Accurate** azimuth move to count (4330→4330, 4341→4336 starting from 10 away). |
| `elevwatch <secs>` | ❌ | Samples for `<secs>` s, then `Average: 1455.00, SD: 0.00, Max: 1455, Min: 1455`. |
| `azimwatch <secs>` | ❌ | Same for heading. SD is about 2–3 counts at rest. |

The help text says `elacc`/`azacc` take an "angle", but **both take raw counts** (`elacc 70` →
`target: 0070`).

### Absolute moves: degrees

| Command | Arg check | Behavior (verified) |
|---|---|---|
| `elangle <deg>` | ❌ | `Dest Angle:   30    Dest Count:        867`, then moves (open-loop quality like `elev`). |
| `azangle <deg>` | ❌ | `Dest Angle:  180    Dest Count:       3533`, then moves. Azimuth is CCW from the coax jack. |

#### Elevation: degrees → counts (linear)

`count ≈ 423 + 14.786 × deg`. Measured: 0°→423, 5°→497, 10°→571, 20°→719, 30°→867,
40°→1014, 50°→1162, 60°→1310, 70°→1458, 80°→1605, 90°→1753. The usual 5–70° limit is enforced
by the app only, not by the firmware. Count 1455 (the parked position today) is about 70°.

#### Azimuth: degrees → counts (piecewise, with a firmware glitch)

| Degrees | Formula | Notes |
|---|---|---|
| 0 – 200 | `725 + 15.6 × deg` | 0→725, 90→2129, 180→3533, 200→3845 |
| 201 – 211 | `3823 + 27.6 × (deg − 201)` | **201° is 22 counts *below* 200°**, and the slope is about 1.8× steeper |
| 211 → 212 | +127 count step | |
| 212 – 1000+ | `4226 + 16.2 × (deg − 212)` | 270→5166, 360→6624, 400→7272, 1000→16992 |

- **No wraparound.** 360° ≠ 0°, and values above 360 are accepted and extrapolated. Azimuth is
  one continuous span limited by cable wrap, not a circle.
- **The 200–212° region is broken in the firmware's conversion** (it isn't monotonic). Any
  `azangle` target in that band lands in the wrong place. This may explain part of the +4° az
  error in `calibration-20261016-164740.json`.
- **Recommended:** do your own degree→count conversion (from a calibration you trust) and drive
  with `azacc`/`elacc`. That bypasses the firmware's tables entirely.

### Relative moves

| Command | Arg check | Behavior (verified) |
|---|---|---|
| `aznudge cw\|ccw` | ✅ | `cw` lowers the count by about 4–5. A bad word (`foo`) silently does nothing. |
| `elnudge up\|down` | ✅ | `up` raises the count by about 4–5. |

Nudges are fixed-length **open-loop pulses**. Right after a direction reversal, the first one or
two nudges move only about 0–2 counts because they're taking up **gear backlash** (roughly 4–8
counts on each axis). Four `elnudge down` in a row moved −19.

### RF

| Command | Arg check | Behavior |
|---|---|---|
| `rfwatch <secs>` | ❌ | Live signal strength, about **20 samples/s**, separated by `ESC[5D`. The app keeps only the first sample. Averaging all of them reduces noise. |
| `scan dump\|reset\|start\|stop` | ✅ | No args gives `Please specify an action (dump or reset)`. `scan start`/`scan stop` were accepted silently, **nothing moved**, and `scan dump` stayed empty. The firmware's own RF scan looks inactive over USB (it may need a receiver to trigger it). `scan reset` **(untested)**. |

### System, setup, and storage

| Command | Arg check | Behavior |
|---|---|---|
| `stat <elements> <window>` | ✅ | `stat 10 3` / `stat 20 5` accepted, no output. Looks like a dead test hook. |
| `nvread <struct> <offset> <count>` | ✅ | Hex dump, at most 24 bytes per call. |
| `nvwrite <struct> <offset> <data bytes>` | ✅ | Usage verified. Writing is **untested** (the byte format, hex vs decimal, is unknown). |
| `gps` | ? | "Store terrestrial location". **Untested.** Probably writes struct 0. The tool permission check blocked it this session. |
| `cal` | ? | "Calibrate tilt sensor". **Untested.** Probably rewrites struct 0's calibration points. |
| `reset` | ? | "Reset the system". **Untested.** |
| `ver`, `help` | n/a | As above. |

## NVRAM map

| structID | Status |
|---|---|
| 0 | Data (36 bytes used, rest erased `0xff`) |
| 1 | Present but fully erased |
| 2–15 | `Read failed` |

Struct 0, little-endian (meanings **inferred**):

| Offset | Type | Value | Likely meaning |
|---|---|---|---|
| 0 | float32 | 25.5 | Stored location (from `gps`)? Doesn't match this site |
| 4 | float32 | 45.8 | Stored location, other coordinate? |
| 8–12 | u16 ×3 | 2456, 922, 0 | Tilt calibration point? |
| 14–18 | u16 ×3 | 2289, 855, 0 | Tilt calibration point? |
| 20–24 | u16 ×3 | 2120, 762, 0 | Tilt calibration point? |
| 26 | u16 | 3800 | Limit or reference? |
| 28 | u16 | 1000 | Limit or reference? |
| 30 | u16 | 1 | Valid/calibrated flag? |
| 32, 34 | u16 | 0xffff, 0 | |

**Backup (restore with `nvwrite 0 <offset> ...` if `cal`/`gps` ever corrupts it):**
```
00 00 cc 41 33 33 37 42 98 09 9a 03 00 00 f1 08 57 03 00 00 48 08 fa 02
00 00 d8 0e e8 03 01 00 ff ff 00 00
```
It was unchanged after all of today's testing.

## Practical takeaways for full control

The app does 1 and 2 already: `dish_motion.deg_to_count()` / `AZ_COUNT_TABLE` hold the
conversion, and every move goes out as `azacc`/`elacc`.

1. **Move with `elacc`/`azacc` in raw counts** and do your own degree conversion. That's
   accurate to ±2, skips the broken 200–212° az table, and avoids the open-loop `*angle` commands.
2. **Verify with `elevwatch 1` / `azimwatch 1`.** SD > 0 on elevation means it is still moving.
3. **Validate every number before sending.** The firmware executes garbage as 0 and doesn't
   clamp range.
4. **Cancel a move by sending a new target** (e.g. the current count).
5. **Approach the final position from the same direction** (or overshoot and come back) to take
   up the 4–8 counts of backlash.
6. **Average `rfwatch` samples.**
7. **Read replies past `GO>`** to catch the async `Stopped at` line, or flush before each command.

## Getting the actual code

The USB console has no command that reads program memory, and no firmware image or source is on
this machine. To see the actual command-handler logic, read the PIC over ICSP with a PICkit
(3/4/5) and disassemble it. That only works if code protection wasn't enabled at the factory.

## Still unexplored

`gps`, `cal`, `reset`, `scan reset`, actual `nvwrite` writes, and destructive-sounding hidden
names. Each one changes stored state or restarts the board, so each needs an explicit go-ahead.
