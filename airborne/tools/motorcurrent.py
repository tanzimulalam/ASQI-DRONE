"""Compare the four motors by the current they draw. PROPS OFF.

Each motor is run on its own at the same throttle while the flight controller's
power monitor is sampled. Four healthy motors draw nearly the same current. A
motor or ESC that is failing under load draws noticeably less, or unsteadily,
and that is the one that could not hold the aircraft up.

This is the test that "it spins freely by hand" cannot do.

    python3 /tmp/motorcurrent.py --throttle 25 --seconds 4

Runs over the flight controller's second USB port. The flight daemon keeps its
own port and does not need stopping. Refuses to run while armed.
"""
import argparse
import glob
import sys
import time

from pymavlink import mavutil

ap = argparse.ArgumentParser()
ap.add_argument("--throttle", type=int, default=25, help="percent")
ap.add_argument("--seconds", type=int, default=4)
ap.add_argument("--gap", type=float, default=3.0)
args = ap.parse_args()

# ArduPilot motor test order -> (letter, output, where it is in the FLIGHT
# CONTROLLER's frame, where it is in the pilot's frame given the board faces
# the lidar end).
ORDER = {
    1: ("A", 1, "FC front-right", "your rear-left"),
    2: ("B", 4, "FC rear-right", "your front-left"),
    3: ("C", 2, "FC rear-left", "your front-right"),
    4: ("D", 3, "FC front-left", "your REAR-RIGHT"),
}

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
if hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
    sys.exit("vehicle is ARMED. Disarm first.")

m.mav.request_data_stream_send(m.target_system, m.target_component,
                               mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)


def battery(seconds):
    """Mean current (A) and lowest voltage (V) over a window."""
    cur, volt = [], []
    t0 = time.time()
    while time.time() - t0 < seconds:
        msg = recv(["SYS_STATUS"])
        if msg:
            if msg.current_battery >= 0:
                cur.append(msg.current_battery / 100.0)
            volt.append(msg.voltage_battery / 1000.0)
    return (sum(cur) / len(cur) if cur else 0.0), (min(volt) if volt else 0.0)


print("PROPS OFF. Each motor runs alone at %d%% for %d s.\n" % (args.throttle, args.seconds))
idle_a, idle_v = battery(3)
print("idle draw: %.2f A at %.2f V\n" % (idle_a, idle_v))

results = {}
for order in (1, 2, 3, 4):
    letter, output, fc_where, your_where = ORDER[order]
    print("  %s (output %d, %s = %s) ... " % (letter, output, fc_where, your_where), end="", flush=True)
    m.mav.command_long_send(m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST, 0,
                            order,                                   # motor, test order
                            mavutil.mavlink.MOTOR_TEST_THROTTLE_PERCENT,
                            args.throttle, args.seconds, 0, 0, 0)
    time.sleep(1.0)                       # let it spool up before measuring
    amps, volts = battery(max(1.0, args.seconds - 1.5))
    results[order] = amps - idle_a
    print("%.2f A above idle   (%.2f V under load)" % (results[order], volts))
    time.sleep(args.gap)

print("\n--- comparison ---")
vals = [v for v in results.values() if v > 0]
if not vals:
    print("no current measured. Is the flight battery connected, and BATT_MONITOR set?")
    raise SystemExit(1)
avg = sum(vals) / len(vals)
for order in (1, 2, 3, 4):
    letter, output, fc_where, your_where = ORDER[order]
    a = results[order]
    pct = 100.0 * (a - avg) / avg if avg else 0
    flag = ""
    if a < avg * 0.75:
        flag = "  <-- WEAK, this is the suspect"
    elif a > avg * 1.25:
        flag = "  <-- drawing much more than the others"
    print("  %s  output %d  %-16s %5.2f A  %+5.0f%% vs average%s"
          % (letter, output, your_where, a, pct, flag))
print("\naverage %.2f A. Healthy motors sit within about 10 percent of each other." % avg)
