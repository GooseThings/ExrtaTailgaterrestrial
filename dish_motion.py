# dish_motion.py
# Shared helper for speed-limited, verified dish moves.
#
# The Tailgater's rubber drive wheels can slip on long, fast single-command
# moves (e.g. commanding azangle/elangle across tens of degrees at once),
# even though short moves track accurately. A single elangle/azangle command
# gives no feedback on whether it actually got there - the firmware just
# acks with the angle it's aiming for ("Dest Count").
#
# stepped_move() breaks a move into smaller chunks and checks that same
# "Dest Count" against the settled raw sensor reading after each chunk,
# so a stall/slip gets caught instead of silently trusted.

import re
import time

DEFAULT_STEP_SIZE = 5.0   # degrees per step
DEFAULT_TOLERANCE = 20    # raw sensor counts
DEFAULT_RETRIES = 2

MIN_ELEVATION = 5
MAX_ELEVATION = 70


def _send(dish, text):
    for ch in text:
        dish.write(ch.encode())
    dish.write(b'\r')
    dish.flush()


def _read(dish, settle):
    time.sleep(settle)
    return dish.read(2000).decode(errors='replace')


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


def _cancelled(cancel_event):
    return cancel_event is not None and cancel_event.is_set()


def stepped_move(dish, axis, current_deg, target_deg,
                  step_size=DEFAULT_STEP_SIZE, tolerance=DEFAULT_TOLERANCE,
                  retries=DEFAULT_RETRIES, on_warning=None, force=False,
                  cancel_event=None):
    """
    Move `axis` ('az' or 'el') from current_deg to target_deg in steps of
    at most `step_size` degrees, verifying the firmware's commanded
    "Dest Count" against the settled raw sensor reading after each step.

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
        move_cmd, query_cmd = 'azangle', 'azim'
        target_deg = max(0, min(360, target_deg))
    elif axis == 'el':
        move_cmd, query_cmd = 'elangle', 'elev'
        target_deg = max(MIN_ELEVATION, min(MAX_ELEVATION, target_deg))
    else:
        raise ValueError(f"Unknown axis: {axis}")

    step_size = max(1.0, step_size)
    targets = _step_targets(current_deg, target_deg, step_size)
    if not targets:
        if not force:
            return target_deg
        targets = [round(target_deg)]

    settle = max(1.5, step_size * 0.15) + 1.0

    for step_target in targets:
        if _cancelled(cancel_event):
            break
        attempt = 0
        while True:
            if _cancelled(cancel_event):
                break
            dish.reset_input_buffer()
            _send(dish, f"{move_cmd} {step_target}")
            ack = _read(dish, 1.0)
            m = re.search(r'Dest Count:\s*(\d+)', ack)
            dest_count = int(m.group(1)) if m else None

            time.sleep(settle)
            raw = _query_raw(dish, query_cmd)
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
                     on_warning=None, settle=1.5, retries=DEFAULT_RETRIES,
                     cancel_event=None):
    """
    Re-affirm a position (re-sending the same target - a no-op if the dish
    is genuinely already there) and check the firmware's commanded
    "Dest Count" against the settled raw reading.

    Meant for direction-reversal points in a serpentine sweep, where gear
    lash/backlash on a worn drivetrain is most likely to introduce slip
    that a per-degree sweep command wouldn't otherwise catch.

    An unreadable sensor reply is retried rather than treated as a passing
    check; if it never resolves, a "could not verify" warning is raised
    instead of silently reporting no issue.

    Returns the raw-count diff (None if cancelled or never readable).
    """
    if axis == 'az':
        move_cmd, query_cmd = 'azangle', 'azim'
    elif axis == 'el':
        move_cmd, query_cmd = 'elangle', 'elev'
    else:
        raise ValueError(f"Unknown axis: {axis}")

    attempt = 0
    while True:
        if _cancelled(cancel_event):
            return None

        dish.reset_input_buffer()
        _send(dish, f"{move_cmd} {round(expected_deg)}")
        ack = _read(dish, 1.0)
        m = re.search(r'Dest Count:\s*(\d+)', ack)
        dest_count = int(m.group(1)) if m else None

        time.sleep(settle)
        raw = _query_raw(dish, query_cmd)
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
