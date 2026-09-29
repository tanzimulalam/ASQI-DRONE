"""Restore Piper to outdoor configuration: geofence on, all pre-arm checks on.

Indoors there is no GPS, so two things have to change:

  FENCE_ENABLE = 0   a geofence needs a position estimate, so with the fence on
                     the aircraft cannot arm indoors at all
  ARMING_CHECK       drop only the GPS-dependent checks, keep everything else

ARMING_CHECK is a bitmask. This sets 11766, which keeps: barometer, compass,
INS, parameters, RC channels, board voltage, battery level, logging, hardware
safety switch and system checks. It drops: GPS lock, GPS configuration, mission
and rangefinder. That is much safer than the usual ARMING_CHECK=0, which turns
off the battery and RC checks too.

RESTORE WITH piper_outdoor.py BEFORE THE AIRCRAFT FLIES OUTSIDE AGAIN.
"""
import sys, time
from pymavlink import mavutil

import glob as _glob
# second USB interface of the flight controller, by stable name: the daemon
# owns the first one and the ttyACM numbers move on every replug.
PORT = sorted(_glob.glob("/dev/serial/by-id/usb-Holybro_Pixhawk6C_*-if02"))[0]
INDOOR_MASK = 1.0
WANT = [("FENCE_ENABLE", 1.0), ("ARMING_CHECK", 1.0),
        ("FLTMODE1", 0.0), ("FLTMODE6", 5.0)]  # STABILIZE up, LOITER down


def connect(timeout=20):
    m = mavutil.mavlink_connection(PORT, baud=115200, source_system=254)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=2)
        except TypeError:
            continue
        if hb:
            return m, hb
    return m, None


def recv(m, types, to=1):
    try:
        return m.recv_match(type=types, blocking=True, timeout=to)
    except TypeError:
        return None


def read(m, name):
    for _ in range(4):
        m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
        t0 = time.time()
        while time.time() - t0 < 2:
            q = recv(m, ["PARAM_VALUE"])
            if q and q.param_id == name:
                return q.param_value
    return None


m, hb = connect()
if hb is None:
    sys.exit("no heartbeat on %s" % PORT)
if hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
    sys.exit("vehicle is ARMED. Nothing changed.")

print("== restoring outdoor configuration ==")
bad = []
for name, val in WANT:
    before = read(m, name)
    m.mav.param_set_send(m.target_system, m.target_component, name.encode(), val,
                         mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    time.sleep(0.5)
    after = read(m, name)
    ok = after is not None and abs(after - val) < 0.5
    if not ok:
        bad.append(name)
    print("  %-14s %-10s -> %-10s %s" % (name, before, after, "OK" if ok else "FAILED"))
if bad:
    sys.exit("FAILED: %s" % ", ".join(bad))

print()
print("== pre-arm now (press the safety switch first for a true reading) ==")
m.mav.command_long_send(m.target_system, m.target_component,
                        mavutil.mavlink.MAV_CMD_RUN_PREARM_CHECKS, 0, 0, 0, 0, 0, 0, 0, 0)
seen = []
t0 = time.time()
while time.time() - t0 < 10:
    msg = recv(m, ["STATUSTEXT"])
    if msg and msg.text not in seen:
        seen.append(msg.text)
if seen:
    for t in seen[-10:]:
        print("    ", t)
else:
    print("     nothing reported: every enabled check passed")

print()
print("Piper is back in OUTDOOR configuration: fence on, all checks on.")
print("Both values above must read 1. If they do not, do not fly.")
