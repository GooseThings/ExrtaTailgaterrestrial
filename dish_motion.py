# dish_motion.py
# Shared helper for speed-limited, verified dish moves.
#
# The Tailgater's rubber drive wheels can slip on long, fast single-command
# moves, even though short moves track accurately. stepped_move() breaks a
# move into smaller chunks and checks the settled raw sensor reading after
# each one, so a stall/slip gets caught instead of silently trusted.
#
# Moves are sent as raw sensor counts via the firmware's closed-loop
# "accurate" commands (azacc/elacc, which land within ~2 counts) rather than
# azangle/elangle, using our own degree->count conversion below. The
# firmware's own azangle table is broken between 200 and 212 deg (201 maps
# *below* 200, then jumps 127 counts at 212) - see docs/FIRMWARE.md.

import re
import time

DEFAULT_STEP_SIZE = 5.0   # degrees per step
DEFAULT_TOLERANCE = 20    # raw sensor counts
DEFAULT_RETRIES = 2

MIN_ELEVATION = 5
MAX_ELEVATION = 70

# Elevation counts are linear in degrees; this reproduces the firmware's
# elangle "Dest Count" exactly for 0-90 deg.
EL_COUNT_AT_0 = 423
EL_COUNTS_PER_DEG = 14.78

# (dish-frame azimuth deg, raw count) breakpoints, interpolated linearly.
# These match the firmware's azangle "Dest Count" exactly outside 200-212
# deg; across that band the firmware's non-monotonic segment is replaced by
# a straight bridge between its two good endpoints.
AZ_COUNT_TABLE = [
    (0, 725),
    (200, 3845),
    (212, 4226),
    (360, 6624),
]

# A move that hasn't reported "Stopped at" by then is treated as stalled.
MOVE_TIMEOUT = 30.0  # seconds


def deg_to_count(axis, deg):
    """Raw sensor count for a dish-frame angle: az in [0, 360) (CCW from
    the coax jack), el clamped to MIN/MAX_ELEVATION."""
    if axis == 'el':
        deg = max(MIN_ELEVATION, min(MAX_ELEVATION, deg))
        return round(EL_COUNT_AT_0 + EL_COUNTS_PER_DEG * deg)
    if axis == 'az':
        deg = deg % 360
        for (d0, c0), (d1, c1) in zip(AZ_COUNT_TABLE, AZ_COUNT_TABLE[1:]):
            if deg <= d1:
                return round(c0 + (c1 - c0) * (deg - d0) / (d1 - d0))
    raise ValueError(f"Unknown axis: {axis}")


def move_command(axis, deg):
    """Firmware command that drives `axis` to `deg`, and its target count."""
    count = deg_to_count(axis, deg)
    return f"{'azacc' if axis == 'az' else 'elacc'} {count}", count


def _send(dish, text):
    for ch in text:
        dish.write(ch.encode())
    dish.write(b'\r')
    dish.flush()


def _read(dish, settle):
    time.sleep(settle)
    return dish.read(2000).decode(errors='replace')


def _wait_stopped(dish, axis, cancel_event):
    """Read until the firmware's async "Stopped at Az/El: N" line, the
    timeout, or a cancel. Returns True if the move reported completion."""
    label = 'Az' if axis == 'az' else 'El'
    buf = ''
    deadline = time.time() + MOVE_TIMEOUT
    while time.time() < deadline:
        if _cancelled(cancel_event):
            return False
        buf += dish.read(256).decode(errors='replace')
        if f'Stopped at {label}:' in buf:
            return True
    return False


def _move_and_check(dish, axis, deg, cancel_event):
    """Send one closed-loop move and return (target_count, settled_raw).
    Either is None if the firmware's ack/sensor reply was unreadable, or
    if the ack echoed a different target than we sent (a dropped serial
    character can turn e.g. 4341 into 441)."""
    cmd, target = move_command(axis, deg)
    dish.reset_input_buffer()
    _send(dish, cmd)
    ack = _read(dish, 0.3)
    m = re.search(r'target:\s*(\d+)', ack)
    if not m or int(m.group(1)) != target:
        # A new move preempts the old one, so re-commanding the current
        # position cancels a garbled target before it travels far.
        _send(dish, 'azim' if axis == 'az' else 'elev')
        current = _read(dish, 0.3)
        c = re.search(r':\s*(\d+)', current)
        if c:
            _send(dish, f"{'azim' if axis == 'az' else 'elev'} {int(c.group(1))}")
        return None, None
    _wait_stopped(dish, axis, cancel_event)
    raw = _query_raw(dish, 'azim' if axis == 'az' else 'elev')
    return target, raw


def _query_raw(dish, query_cmd):
    dish.reset_input_buffer()
    _send(dish, query_cmd)
    reply = _read(dish, 0.5)
    for line in reply.splitlines():
        if ':' in line:
            try:
                return int(line.split(':')[-1].strip())
            except ValueError:
                pass
    return None


def _step_targets(start, end, step):
    start = round(start)
    end = round(end)
    if start == end:
        return []
    direction = 1 if end > start else -1
    targets = []
    pos = start
    while True:
        pos += direction * step
        if (direction > 0 and pos >= end) or (direction < 0 and pos <= end):
            targets.append(end)
            break
        targets.append(round(pos))
    return targets


def az_distance(a, b):
    """Shortest angular distance between two azimuths (0-360, wraps at
    North), e.g. az_distance(359, 1) == 2, not 358."""
    return abs(((a - b + 180) % 360) - 180)


def _step_targets_az(start, end, step):
    """Like _step_targets, but takes the shorter way around through
    0/360 (North) instead of always counting up or down - a dish slewing
    from 350 to 10 should go 350 -> 360/0 -> 10 (20 degrees), not loop the
    long way through 180 (340 degrees)."""
    start = round(start) % 360
    end = round(end) % 360
    delta = ((end - start + 180) % 360) - 180  # shortest signed delta, (-180, 180]
    if delta == 0:
        return []
    direction = 1 if delta > 0 else -1
    remaining = abs(delta)
    targets = []
    pos = start
    while remaining > 0:
        move = min(step, remaining)
        pos = (pos + direction * move) % 360
        remaining -= move
        targets.append(round(pos) % 360)
    return targets


def _cancelled(cancel_event):
    return cancel_event is not None and cancel_event.is_set()


def stepped_move(dish, axis, current_deg, target_deg,
                  step_size=DEFAULT_STEP_SIZE, tolerance=DEFAULT_TOLERANCE,
                  retries=DEFAULT_RETRIES, on_warning=None, force=False,
                  cancel_event=None):
    """
    Move `axis` ('az' or 'el') from current_deg to target_deg in steps of
    at most `step_size` degrees, verifying each step's target count
    against the settled raw sensor reading.

    Calls on_warning(dict) once per step that doesn't settle within
    `tolerance` raw counts after `retries` resends - including when the
    sensor reply itself was unreadable (missing/garbled), which is treated
    as a retry-worthy failure rather than silently passing.

    `force=True` always sends at least one direct move to target_deg, even
    if current_deg == target_deg - use this when current_deg is a fallback
    guess rather than a real known position (e.g. right after a restart),
    so an actually-elsewhere dish still gets commanded instead of the move
    being silently skipped.

    `cancel_event`, if given, is checked before each step and each resend -
    if set, the move stops early (e.g. a scan/track Stop request) instead
    of running all remaining steps before noticing.

    Returns target_deg (the requested position - this tracks the *intended*
    degree, not a true measured one, since there's no raw-count-to-degree
    inverse available; it's meant as a continuity baseline for the next
    move, not an absolute position reading).
    """
    if axis == 'az':
        target_deg = target_deg % 360
    elif axis == 'el':
        target_deg = max(MIN_ELEVATION, min(MAX_ELEVATION, target_deg))
    else:
        raise ValueError(f"Unknown axis: {axis}")

    step_size = max(1.0, step_size)
    if axis == 'az':
        targets = _step_targets_az(current_deg, target_deg, step_size)
    else:
        targets = _step_targets(current_deg, target_deg, step_size)
    if not targets:
        if not force:
            return target_deg
        targets = [round(target_deg) % 360 if axis == 'az' else round(target_deg)]

    for step_target in targets:
        if _cancelled(cancel_event):
            break
        attempt = 0
        while True:
            if _cancelled(cancel_event):
                break
            dest_count, raw = _move_and_check(dish, axis, step_target, cancel_event)
            unreadable = raw is None or dest_count is None
            diff = None if unreadable else (raw - dest_count)

            if not unreadable and abs(diff) <= tolerance:
                break
            attempt += 1
            if attempt > retries:
                if on_warning:
                    on_warning({
                        "axis": axis,
                        "target_deg": step_target,
                        "dest_count": dest_count,
                        "settled_raw": raw,
                        "diff": diff,
                        "unverified": unreadable,
                        "time": time.time(),
                    })
                break
            # otherwise retry this same step target

    return target_deg


def verify_position(dish, axis, expected_deg, tolerance=DEFAULT_TOLERANCE,
                     on_warning=None, retries=DEFAULT_RETRIES,
                     cancel_event=None):
    """
    Re-affirm a position (re-sending the same target - a no-op if the dish
    is genuinely already there) and check its target count against the
    settled raw reading.

    Meant for direction-reversal points in a serpentine sweep, where gear
    lash/backlash on a worn drivetrain is most likely to introduce slip
    that a per-degree sweep command wouldn't otherwise catch.

    An unreadable sensor reply is retried rather than treated as a passing
    check; if it never resolves, a "could not verify" warning is raised
    instead of silently reporting no issue.

    Returns the raw-count diff (None if cancelled or never readable).
    """
    if axis not in ('az', 'el'):
        raise ValueError(f"Unknown axis: {axis}")

    attempt = 0
    while True:
        if _cancelled(cancel_event):
            return None

        dest_count, raw = _move_and_check(dish, axis, round(expected_deg), cancel_event)
        unreadable = raw is None or dest_count is None
        diff = None if unreadable else (raw - dest_count)

        if not unreadable and abs(diff) <= tolerance:
            return diff
        attempt += 1
        if attempt > retries:
            if on_warning:
                on_warning({
                    "axis": axis,
                    "target_deg": expected_deg,
                    "dest_count": dest_count,
                    "settled_raw": raw,
                    "diff": diff,
                    "unverified": unreadable,
                    "time": time.time(),
                })
            return diff
        # otherwise retry
