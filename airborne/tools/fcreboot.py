#!/usr/bin/env python3
"""Reboot the flight controller over MAVLink.

Run on the drone Jetson with the control daemon stopped, since it owns the port:

    sudo systemctl stop drone-airborne
    python3 tools/fcreboot.py
    sudo systemctl start drone-airborne

Some settings do not take effect until the flight controller restarts, compass
configuration among them. This saves unplugging the USB lead to power-cycle it.

Refuses to run while the aircraft is armed.
"""
import sys, time
from pymavlink import mavutil

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"

def recv(m, **kw):
    try: return m.recv_match(**kw)
    except TypeError: return None

print(f"connecting to {DEV} ...", file=sys.stderr)
m = mavutil.mavlink_connection(DEV, baud=115200)
hb = None; end = time.time() + 30
while hb is None and time.time() < end:
    try: hb = m.wait_heartbeat(timeout=5)
    except TypeError: continue
if hb is None:
    sys.exit("no heartbeat")
if hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
    sys.exit("vehicle is ARMED. Refusing to reboot the flight controller.")
print(f"  FC up: sys={m.target_system}", file=sys.stderr)

m.mav.command_long_send(
    m.target_system, m.target_component,
    mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
    0, 1, 0, 0, 0, 0, 0, 0)     # param1=1 -> reboot autopilot
print("  reboot command sent; waiting for it to come back ...", file=sys.stderr)

time.sleep(3)
try: m.close()
except Exception: pass

# Reconnect to prove it actually restarted rather than just accepting the command.
end = time.time() + 45
while time.time() < end:
    try:
        m2 = mavutil.mavlink_connection(DEV, baud=115200)
        hb2 = None; d = time.time() + 10
        while hb2 is None and time.time() < d:
            try: hb2 = m2.wait_heartbeat(timeout=5)
            except TypeError: continue
        if hb2 is not None:
            print("  flight controller is back up")
            sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit("flight controller did not come back within 45s")
