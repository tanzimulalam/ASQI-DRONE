#!/usr/bin/env python3
"""Watch raw RC input from the transmitter, as the flight controller sees it.

Run on the DRONE Jetson. The control daemon holds /dev/ttyACM0 exclusively:

    sudo systemctl stop drone-airborne
    python3 tools/rcwatch.py --seconds 15
    sudo systemctl start drone-airborne

Reports, per channel, the current value and the min/max seen during the window,
so moving a stick shows up as a wide span while an untouched channel stays flat.

This is the Stage 3 tool. Checking override direction means knowing which
physical channel actually moved, and "I pushed right and it felt right" is not
that. Note the FC reports *overridden* values on ch1-4 while the daemon is
piloting, which is exactly why the daemon must be stopped for an honest reading
of the transmitter.
"""
from __future__ import annotations

import argparse
import sys
import time

from pymavlink import mavutil

DEFAULT_DEVICE = "/dev/ttyACM0"
DEFAULT_BAUD = 115200
# RC_CHANNELS carries 18; anything past what the link actually delivers reads 0.
MAX_CHANNELS = 16


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", default=DEFAULT_DEVICE)
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    p.add_argument("--seconds", type=float, default=12.0)
    args = p.parse_args()

    print(f"connecting to {args.device} ...", file=sys.stderr)
    m = mavutil.mavlink_connection(args.device, baud=args.baud)
    if m.wait_heartbeat(timeout=30) is None:
        sys.exit("no heartbeat")
    print(f"  FC up: sys={m.target_system}\n", file=sys.stderr)
    print(f"MOVE EVERY STICK AND SWITCH NOW - sampling for {args.seconds:.0f}s\n",
          file=sys.stderr)

    lo = [None] * MAX_CHANNELS
    hi = [None] * MAX_CHANNELS
    cur = [0] * MAX_CHANNELS
    frames = 0

    deadline = time.time() + args.seconds
    while time.time() < deadline:
        try:
            msg = m.recv_match(type="RC_CHANNELS", blocking=True, timeout=2)
        except TypeError:
            continue  # pymavlink instance-tracking defect; next frame is fine
        if msg is None:
            continue
        frames += 1
        for i in range(MAX_CHANNELS):
            v = getattr(msg, f"chan{i + 1}_raw", 0)
            cur[i] = v
            if v == 0:
                continue  # channel not present on this link
            lo[i] = v if lo[i] is None else min(lo[i], v)
            hi[i] = v if hi[i] is None else max(hi[i], v)

    if frames == 0:
        print("\nNo RC_CHANNELS received at all.", file=sys.stderr)
        print("The receiver is not talking to the flight controller.", file=sys.stderr)
        return 1

    live = [i for i in range(MAX_CHANNELS) if lo[i] is not None]
    moved = [i for i in live if (hi[i] - lo[i]) > 40]

    print(f"{frames} frames over {args.seconds:.0f}s\n")
    print(f"{'ch':>3}  {'now':>5}  {'min':>5}  {'max':>5}  {'span':>5}   ")
    print("-" * 40)
    for i in live:
        span = hi[i] - lo[i]
        mark = "  <- moved" if span > 40 else ""
        print(f"{i + 1:>3}  {cur[i]:>5}  {lo[i]:>5}  {hi[i]:>5}  {span:>5}{mark}")

    print(f"\nchannels present: {len(live)}  ({', '.join(str(i + 1) for i in live)})")
    print(f"channels that moved: {', '.join(str(i + 1) for i in moved) or 'none'}")
    if len(live) < 9:
        print(f"\nNote: only {len(live)} channels are reaching the FC. The pilot-takeover"
              "\nfeature needs spare channels above 8 to mirror the sticks onto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
