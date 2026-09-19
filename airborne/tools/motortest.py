#!/usr/bin/env python3
"""Spin each motor on its own, at matched throttle, so they can be compared.

PROPS OFF. This spins motors with no pre-arm checks in the way. Nothing here
refuses to run because a propeller is fitted, because nothing here can tell.

Run on the DRONE Jetson. The control daemon holds /dev/ttyACM0 exclusively, so
stop it first:

    sudo systemctl stop drone-airborne
    python3 tools/motortest.py --throttle 8 --seconds 2
    python3 tools/motortest.py --motor 4 --throttle 12
    sudo systemctl start drone-airborne

Why this exists: `Potential Thrust Loss (N)` in flight-mode testing cannot tell
you whether motor N is weak or merely the first to saturate, because with props
off the airframe never responds and the attitude controller winds up regardless.
Driving one motor at a time at an identical throttle removes the controller from
the question. Listen and watch: all four should sound and spool the same.

Motors are addressed in ArduPilot's test order (A, B, C, D ...), which is not the
same as the motor numbering in `Potential Thrust Loss (N)`. Test order A is the
front-right on a quad X. The mapping is printed below so the two can be lined up.

This refuses to run while the aircraft is armed.
"""
from __future__ import annotations

import argparse
import sys
import time

from pymavlink import mavutil

DEFAULT_DEVICE = "/dev/ttyACM0"
DEFAULT_BAUD = 115200

# ArduCopter quad X: test order letter -> (output number, position).
QUAD_X_ORDER = {
    1: ("A", 1, "front right"),
    2: ("B", 4, "rear right"),
    3: ("C", 2, "rear left"),
    4: ("D", 3, "front left"),
}


def recv(m, **kwargs):
    """recv_match that survives pymavlink's instance-tracking bug (see params.py)."""
    try:
        return m.recv_match(**kwargs)
    except TypeError:
        return None


def connect(device: str, baud: int, timeout: float = 30.0):
    print(f"connecting to {device} ...", file=sys.stderr)
    m = mavutil.mavlink_connection(device, baud=baud)
    deadline = time.time() + timeout
    hb = None
    while hb is None and time.time() < deadline:
        try:
            hb = m.wait_heartbeat(timeout=5)
        except TypeError:
            continue
    if hb is None:
        sys.exit(f"no heartbeat from {device} within {timeout:.0f}s")
    print(f"  flight controller up: sys={m.target_system} comp={m.target_component}",
          file=sys.stderr)
    return m


def refuse_if_armed(m) -> None:
    hb = recv(m, type="HEARTBEAT", blocking=True, timeout=5)
    if hb is None:
        sys.exit("no heartbeat to check arm state; refusing to spin motors blind")
    if bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
        sys.exit("vehicle is ARMED. Disarm before running a motor test.")


def spin(m, order: int, throttle: int, seconds: int) -> bool:
    """Run one motor in test order at a throttle percentage. Confirms from the ack."""
    letter, output, where = QUAD_X_ORDER.get(order, ("?", order, "unknown"))
    print(f"  {letter} (output {output}, {where}): {throttle}% for {seconds}s ... ",
          end="", flush=True)
    m.mav.command_long_send(
        m.target_system,
        m.target_component,
        mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
        0,
        float(order),                                   # motor, in test order
        float(mavutil.mavlink.MOTOR_TEST_THROTTLE_PERCENT),
        float(throttle),
        float(seconds),
        0, 0, 0,
    )
    # Collect STATUSTEXT as well as the ack: a rejection without the vehicle's own
    # reason ("Motor Test: Safety switch", "Motor Test: RC not calibrated") sends
    # you guessing, and those two causes look identical from the result code.
    deadline = time.time() + 5
    result = None
    reasons = []
    while time.time() < deadline:
        msg = recv(m, type=["COMMAND_ACK", "STATUSTEXT"], blocking=True, timeout=1)
        if msg is None:
            continue
        if msg.get_type() == "STATUSTEXT":
            text = msg.text.decode() if isinstance(msg.text, bytes) else msg.text
            text = text.replace(chr(0), "").strip()
            if text:
                reasons.append(text)
            continue
        if msg.command == mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST:
            result = msg.result
            break

    if result is None:
        print("NO ACK")
    elif result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
        print("accepted")
    else:
        print(f"REJECTED (result {result})")

    # Give the vehicle a moment to finish explaining itself.
    grace = time.time() + 1.5
    while time.time() < grace:
        msg = recv(m, type="STATUSTEXT", blocking=True, timeout=0.5)
        if msg is None:
            continue
        text = msg.text.decode() if isinstance(msg.text, bytes) else msg.text
        text = text.replace(chr(0), "").strip()
        if text and text not in reasons:
            reasons.append(text)

    for r in reasons:
        print(f"      vehicle says: {r}")

    return result == mavutil.mavlink.MAV_RESULT_ACCEPTED


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--motor", type=int, default=0, metavar="N",
                   help="test order 1-4 (A-D); default 0 runs all four in sequence")
    p.add_argument("--throttle", type=int, default=8, metavar="PCT",
                   help="throttle percent, default 8. Keep it low.")
    p.add_argument("--seconds", type=int, default=2, metavar="S",
                   help="seconds per motor, default 2")
    p.add_argument("--gap", type=float, default=2.0, metavar="S",
                   help="pause between motors, default 2.0")
    p.add_argument("--device", default=DEFAULT_DEVICE)
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = p.parse_args()

    if not 1 <= args.throttle <= 25:
        sys.exit("throttle must be 1-25 percent; this is a bench test, not a run-up")

    print("PROPS OFF. This spins motors.", file=sys.stderr)

    m = connect(args.device, args.baud)
    refuse_if_armed(m)

    order = [args.motor] if args.motor else [1, 2, 3, 4]
    failures = 0
    for i, o in enumerate(order):
        if not spin(m, o, args.throttle, args.seconds):
            failures += 1
        # Let it stop fully before the next one, so what you hear is one motor.
        if i < len(order) - 1:
            time.sleep(args.seconds + args.gap)

    if failures:
        print(f"\n{failures} of {len(order)} not accepted by the vehicle.", file=sys.stderr)
    else:
        print("\nAll commands accepted. Acceptance is not evidence a motor actually "
              "turned: that part is yours to watch and listen to.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
