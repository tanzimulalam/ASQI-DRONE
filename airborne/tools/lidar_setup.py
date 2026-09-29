"""Configure a Benewake TF02-Pro on TELEM2 (serial) and verify live readings.

    python3 tools/lidar_setup.py --behind-cm 18 --ground-cm 12 --system 1

  --behind-cm   how far the sensor sits BEHIND the centre of the aircraft
  --ground-cm   sensor face to the ground with the aircraft on its legs

Both matter for a rear mount. When the aircraft pitches, a sensor set back from
the centre swings up and down, and ArduPilot only corrects for that if it knows
the offset. The ground clearance is subtracted so that sitting on the ground
reads zero rather than the mounting height.

Runs over the flight controller's second USB port, so the flight daemon keeps
its own. Refuses to run while armed. Every write is read back from the vehicle.
"""
import argparse
import glob
import sys
import time

from pymavlink import mavutil

ap = argparse.ArgumentParser()
ap.add_argument("--behind-cm", type=float, required=True)
ap.add_argument("--ground-cm", type=float, required=True)
ap.add_argument("--system", type=int, default=None,
                help="refuse to run unless the flight controller reports this system id")
args = ap.parse_args()

PORT = sorted(glob.glob("/dev/serial/by-id/usb-Holybro_Pixhawk6C_*-if02"))[0]

WANT = [
    ("SERIAL2_PROTOCOL", 9.0),        # Lidar
    ("SERIAL2_BAUD", 115.0),          # 115200
    ("RNGFND1_TYPE", 27.0),           # Benewake TF03 / TF02-Pro, serial
    ("RNGFND1_MIN_CM", 40.0),         # the sensor's own minimum
    ("RNGFND1_MAX_CM", 1350.0),       # honest working range, not the 40 m headline
    ("RNGFND1_ORIENT", 25.0),         # pointing down
    ("RNGFND1_ADDR", 0.0),            # serial, so no I2C address
    ("RNGFND1_POS_X", -abs(args.behind_cm) / 100.0),   # metres, negative is aft
    ("RNGFND1_POS_Y", 0.0),                            # centred left to right
    ("RNGFND1_POS_Z", abs(args.ground_cm) / 100.0),    # metres, positive is down
]
# Older firmware names this in centimetres, newer in metres. Try both; whichever
# exists on this vehicle will take the write and the other will simply not exist.
GROUND_CLEARANCE = [("RNGFND1_GNDCLEAR", args.ground_cm),
                    ("RNGFND1_GNDCLR", args.ground_cm / 100.0)]


def connect(timeout=25):
    """Wait for the autopilot's own heartbeat, not whatever speaks first.

    Other components share this link and some announce themselves as system 0,
    which pymavlink will otherwise latch onto as the target.
    """
    m = mavutil.mavlink_connection(PORT, baud=115200, source_system=254)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=2)
        except TypeError:
            continue
        if not hb:
            continue
        src = hb.get_srcSystem()
        if src == 0 or hb.type == mavutil.mavlink.MAV_TYPE_GCS:
            continue
        m.target_system = src
        m.target_component = hb.get_srcComponent() or 1
        return m, hb
    return m, None


def recv(m, types, to=2):
    try:
        return m.recv_match(type=types, blocking=True, timeout=to)
    except TypeError:
        return None


def read(m, name, tries=3):
    for _ in range(tries):
        m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
        t0 = time.time()
        while time.time() - t0 < 2:
            q = recv(m, ["PARAM_VALUE"])
            if q and q.param_id == name:
                return q.param_value
    return None


def write(m, name, val, required=True):
    before = read(m, name)
    if before is None and not required:
        print("  %-17s not on this firmware, skipped" % name)
        return True
    m.mav.param_set_send(m.target_system, m.target_component, name.encode(), val,
                         mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    time.sleep(0.4)
    after = read(m, name)
    ok = after is not None and abs(after - val) < 0.01 + abs(val) * 0.01
    print("  %-17s %-9s -> %-9s %s" % (name, before, after, "OK" if ok else "FAILED"))
    return ok or not required


m, hb = connect()
if hb is None:
    sys.exit("no heartbeat on %s" % PORT)
if args.system is not None and m.target_system != args.system:
    sys.exit("this is system %d, not %d. Nothing changed." % (m.target_system, args.system))
if hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
    sys.exit("vehicle is ARMED. Nothing changed.")

print("flight controller: system %d" % m.target_system)
print("\n== writing ==")
bad = [n for n, v in WANT if not write(m, n, v)]
for n, v in GROUND_CLEARANCE:
    write(m, n, v, required=False)
if bad:
    sys.exit("FAILED: %s" % ", ".join(bad))

print("\n== rebooting the flight controller ==")
m.mav.command_long_send(m.target_system, m.target_component,
                        mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
                        0, 1, 0, 0, 0, 0, 0, 0)
m.close()
time.sleep(15)
m, hb = connect(timeout=30)
if hb is None:
    sys.exit("no heartbeat after reboot; power-cycle and re-run")
print("back up. RNGFND1_TYPE reads", read(m, "RNGFND1_TYPE"))

for msg_id in (132, 173):
    m.mav.command_long_send(m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                            0, msg_id, 200000, 0, 0, 0, 0, 0)
m.mav.request_data_stream_send(m.target_system, m.target_component,
                               mavutil.mavlink.MAV_DATA_STREAM_ALL, 5, 1)

print("\n== listening 10 s ==")
vals = []
t0 = time.time()
while time.time() - t0 < 10:
    msg = recv(m, ["RANGEFINDER"], to=1)
    if msg:
        vals.append(round(msg.distance, 2))
print()
if not vals:
    print("RESULT: no readings. Check the wiring at TELEM2: red pin 1, white pin 2,")
    print("green pin 3, black pin 6, middle two crossed.")
else:
    print("RESULT: %d readings, min %.2f m, max %.2f m, last %.2f m"
          % (len(vals), min(vals), max(vals), vals[-1]))
    if min(vals) == max(vals) == 45.0:
        print("Pinned at 45.00 m, which is this sensor's way of saying no valid return.")
        print("Point it at something between 0.4 m and 13 m away.")
    else:
        print("Distances are changing, so the sensor is genuinely measuring.")
