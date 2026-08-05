#!/usr/bin/env python3
"""Read and write ArduPilot parameters over the Pixhawk's USB link.

Run on the DRONE Jetson. The control daemon holds /dev/ttyACM0 exclusively, so
stop it first:

    sudo systemctl stop drone-airborne
    python3 tools/params.py get FS_GCS_ENABLE FENCE_ENABLE
    python3 tools/params.py set FS_GCS_ENABLE=1
    sudo systemctl start drone-airborne

Every write is read back from the vehicle and confirmed before this exits
non-zero or zero. A parameter you believe you set but did not is worse than one
you never touched, so nothing here reports success on the strength of having sent
a message.

This deliberately refuses to run while the aircraft is armed.
"""
from __future__ import annotations

import argparse
import sys
import time

from pymavlink import mavutil

DEFAULT_DEVICE = "/dev/ttyACM0"
DEFAULT_BAUD = 115200


def recv(m, **kwargs):
    """recv_match that survives pymavlink's instance-tracking bug.

    Some messages arrive with an instance field while pymavlink's per-type
    `_instances` dict is still None, and its own add_message() then raises
    TypeError from inside recv_match. It is a library defect, not a link problem,
    and the next message parses fine — but uncaught it aborts a parameter write
    midway, leaving you unsure whether the value took. Swallow it and continue.
    """
    try:
        return m.recv_match(**kwargs)
    except TypeError:
        return None


def connect(device: str, baud: int, timeout: float = 30.0):
    print(f"connecting to {device} ...", file=sys.stderr)
    m = mavutil.mavlink_connection(device, baud=baud)
    if m.wait_heartbeat(timeout=timeout) is None:
        sys.exit(f"no heartbeat from {device} within {timeout:.0f}s")
    print(f"  flight controller up: sys={m.target_system} comp={m.target_component}",
          file=sys.stderr)
    return m


def refuse_if_armed(m) -> None:
    """Parameter changes on an armed vehicle are not something to do casually."""
    hb = recv(m, type="HEARTBEAT", blocking=True, timeout=5)
    if hb is None:
        print("  warning: no heartbeat to check arm state", file=sys.stderr)
        return
    armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
    if armed:
        sys.exit("vehicle is ARMED — refusing to touch parameters")


def get_param(m, name: str, timeout: float = 8.0):
    m.mav.param_request_read_send(
        m.target_system, m.target_component, name.encode(), -1
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = recv(m, type="PARAM_VALUE", blocking=True, timeout=1)
        if msg and msg.param_id.strip("\x00") == name:
            return msg.param_value
    return None


def set_param(m, name: str, value: float, timeout: float = 8.0) -> bool:
    before = get_param(m, name)
    m.mav.param_set_send(
        m.target_system,
        m.target_component,
        name.encode(),
        float(value),
        mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = recv(m, type="PARAM_VALUE", blocking=True, timeout=1)
        if msg and msg.param_id.strip("\x00") == name:
            # Confirm from the vehicle's own report, not from having sent it.
            got = msg.param_value
            ok = abs(got - float(value)) < 1e-6
            arrow = f"{before:g} -> {got:g}" if before is not None else f"{got:g}"
            print(f"  {name:24} {arrow}  {'OK' if ok else 'MISMATCH'}")
            return ok
    print(f"  {name:24} NO CONFIRMATION (unchanged?)")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("items", nargs="+", metavar="NAME | NAME=VALUE")
    p.add_argument("--device", default=DEFAULT_DEVICE)
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = p.parse_args()

    m = connect(args.device, args.baud)
    refuse_if_armed(m)

    failures = 0
    if args.action == "get":
        for name in args.items:
            v = get_param(m, name)
            print(f"  {name:24} {'<no response>' if v is None else f'{v:g}'}")
            failures += v is None
    else:
        for item in args.items:
            if "=" not in item:
                sys.exit(f"expected NAME=VALUE, got {item!r}")
            name, _, raw = item.partition("=")
            failures += not set_param(m, name.strip(), float(raw))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
