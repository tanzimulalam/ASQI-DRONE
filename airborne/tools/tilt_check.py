"""Check the flight controller's orientation against the airframe, by hand.

Follow the prompts. Nothing is written; this only reads attitude.

What it proves: if the board is rotated relative to the airframe, ArduPilot's
corrections drive the wrong motors and the aircraft flips shortly after takeoff.
"""
import glob, math, sys, time
from pymavlink import mavutil

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


def sample(seconds, label):
    print("\n>>> %s  (%d s)" % (label, seconds))
    best = (0.0, 0.0)
    t0 = time.time()
    while time.time() - t0 < seconds:
        msg = recv(["ATTITUDE"])
        if not msg:
            continue
        p, r = math.degrees(msg.pitch), math.degrees(msg.roll)
        if abs(p) + abs(r) > abs(best[0]) + abs(best[1]):
            best = (p, r)
    print("    largest tilt seen: pitch %+.1f deg, roll %+.1f deg" % best)
    return best


print("Hold the aircraft. Follow each prompt as it appears.")
sample(6, "HOLD IT LEVEL")
nose_up = sample(8, "LIFT THE NOSE UP (the end away from the lidar), hold it there")
sample(4, "back to LEVEL")
left_up = sample(8, "LIFT THE LEFT SIDE UP (left when facing forward), hold it there")
sample(3, "back to LEVEL")

print("\n--- what this means ---")
ok = True
if nose_up[0] > 10:
    print("  nose up gave POSITIVE pitch: correct, the board faces forward")
elif nose_up[0] < -10:
    print("  nose up gave NEGATIVE pitch: the board is rotated 180 degrees (or you")
    print("  lifted the end the flight controller thinks is the tail)")
    ok = False
else:
    print("  pitch barely moved (%+.1f): not enough tilt to judge, try again" % nose_up[0])
    ok = False

if left_up[1] < -10:
    print("  left side up gave NEGATIVE roll: correct")
elif left_up[1] > 10:
    print("  left side up gave POSITIVE roll: the board is rotated relative to the frame")
    ok = False
else:
    print("  roll barely moved (%+.1f): not enough tilt to judge, try again" % left_up[1])
    ok = False

print()
print("VERDICT: orientation looks correct" if ok else
      "VERDICT: orientation is WRONG or the test was unclear. Do not fly until resolved.")
