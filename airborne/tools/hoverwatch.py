"""Watch the four motor outputs during a hover and call out an imbalance.

Run this, then hover. It prints the four outputs once a second and warns when a
motor is working much harder than the one diagonally opposite it, which is the
signature that preceded the crash: output 3 pinned high while the aircraft rolled
the other way.

Read-only. It cannot stop the aircraft. If it warns, land.

    python3 /tmp/hoverwatch.py           # runs until Ctrl+C
"""
import glob
import math
import sys
import time

from pymavlink import mavutil

WARN_SPREAD = 150      # us between a motor and its diagonal partner
DANGER_SPREAD = 250

PORT = sorted(glob.glob("/dev/serial/by-id/usb-Holybro_Pixhawk6C_*-if02"))[0]
m = mavutil.mavlink_connection(PORT, baud=115200, source_system=254)


def recv(types, to=1):
    try:
        return m.recv_match(type=types, blocking=True, timeout=to)
    except TypeError:
        return None


hb = None
t0 = time.time()
while time.time() - t0 < 20 and hb is None:
    h = recv(["HEARTBEAT"])
    if h and h.get_srcSystem() and h.type != mavutil.mavlink.MAV_TYPE_GCS:
        m.target_system = h.get_srcSystem()
        m.target_component = h.get_srcComponent() or 1
        hb = h
if hb is None:
    sys.exit("no autopilot heartbeat")
m.mav.request_data_stream_send(m.target_system, m.target_component,
                               mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)

print("watching. Outputs are in the flight controller's frame:")
print("  M1 = your rear-left   M2 = your front-right")
print("  M3 = your REAR-RIGHT  M4 = your front-left")
print("Diagonal pairs are M1+M2 and M3+M4.\n")
print(" time   M1   M2   M3   M4  | roll  pitch |  note")

servo = att = None
worst = 0
t0 = time.time()
last = 0.0
try:
    while True:
        msg = recv(["SERVO_OUTPUT_RAW", "ATTITUDE"])
        if msg is None:
            continue
        if msg.get_type() == "SERVO_OUTPUT_RAW":
            servo = msg
        else:
            att = msg
        if not (servo and att):
            continue
        now = time.time()
        if now - last < 1.0:
            continue
        last = now
        o = [servo.servo1_raw, servo.servo2_raw, servo.servo3_raw, servo.servo4_raw]
        if max(o) < 1100:                      # not spinning
            continue
        d1 = abs(o[0] - o[1])                  # M1 vs M2, one diagonal
        d2 = abs(o[2] - o[3])                  # M3 vs M4, the other
        spread = max(d1, d2)
        worst = max(worst, spread)
        note = ""
        if spread > DANGER_SPREAD:
            note = "  *** LAND NOW: %d us imbalance ***" % spread
        elif spread > WARN_SPREAD:
            note = "  warning: %d us imbalance" % spread
        print("%5.0fs %4d %4d %4d %4d | %+5.1f %+5.1f |%s"
              % (now - t0, o[0], o[1], o[2], o[3],
                 math.degrees(att.roll), math.degrees(att.pitch), note))
except KeyboardInterrupt:
    print("\nworst diagonal imbalance seen: %d us" % worst)
    print("under 150 is normal. Over 250 means one motor is not pulling its weight.")
