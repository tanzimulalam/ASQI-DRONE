"""Download the most recent flight log from the flight controller.

Runs on the drone Jetson, over the Pixhawk's second USB port, so the flight
daemon keeps its own port. The log holds per-motor outputs and the attitude the
controller wanted versus the attitude it got, which is what identifies a motor
that stopped producing thrust.

    python3 /tmp/getlog.py            # newest log
    python3 /tmp/getlog.py --list     # just list what is on the card
    python3 /tmp/getlog.py --id 42    # a specific one
"""
import argparse
import glob
import sys
import time

from pymavlink import mavutil

ap = argparse.ArgumentParser()
ap.add_argument("--list", action="store_true")
ap.add_argument("--id", type=int, default=None)
ap.add_argument("--out", default="/tmp/flight.bin")
args = ap.parse_args()

PORT = sorted(glob.glob("/dev/serial/by-id/usb-Holybro_Pixhawk6C_*-if02"))[0]
m = mavutil.mavlink_connection(PORT, baud=115200, source_system=254)


def recv(types, to=2):
    try:
        return m.recv_match(type=types, blocking=True, timeout=to)
    except TypeError:
        return None


hb = None
t0 = time.time()
while time.time() - t0 < 15 and hb is None:
    hb = recv(["HEARTBEAT"])
if hb is None:
    sys.exit("no heartbeat from the flight controller")

# --- list what is on the card ------------------------------------------------
m.mav.log_request_list_send(m.target_system, m.target_component, 0, 0xFFFF)
entries = {}
t0 = time.time()
while time.time() - t0 < 20:
    msg = recv(["LOG_ENTRY"])
    if not msg:
        continue
    if msg.size or msg.num_logs:
        entries[msg.id] = (msg.size, msg.time_utc)
    if len(entries) >= msg.num_logs and msg.num_logs:
        break
if not entries:
    sys.exit("no logs listed; is logging enabled on this flight controller?")

for i in sorted(entries)[-8:]:
    size, when = entries[i]
    print("  log %-4d %8.2f MB" % (i, size / 1e6))
if args.list:
    raise SystemExit

log_id = args.id if args.id is not None else max(entries)
size = entries[log_id][0]
print("\ndownloading log %d (%.2f MB). This is slow over a serial link." % (log_id, size / 1e6))

data = bytearray(size)
have = bytearray(size)
offset = 0
CHUNK = 90
last_report = 0.0
stall = 0
while offset < size:
    m.mav.log_request_data_send(m.target_system, m.target_component, log_id, offset, size - offset)
    got_any = False
    t0 = time.time()
    while time.time() - t0 < 3:
        msg = recv(["LOG_DATA"], to=1)
        if not msg or msg.id != log_id:
            continue
        got_any = True
        chunk = bytes(msg.data[:msg.count])
        data[msg.ofs:msg.ofs + len(chunk)] = chunk
        have[msg.ofs:msg.ofs + len(chunk)] = b"\x01" * len(chunk)
        if msg.ofs + len(chunk) >= offset:
            offset = msg.ofs + len(chunk)
        now = time.time()
        if now - last_report > 5:
            last_report = now
            print("   %5.1f%%  (%.2f of %.2f MB)" % (100.0 * offset / size, offset / 1e6, size / 1e6))
        if offset >= size:
            break
    if not got_any:
        stall += 1
        if stall > 10:
            print("   stalled at %.1f%%; writing what we have" % (100.0 * offset / size))
            break
    else:
        stall = 0

missing = have.count(0)
with open(args.out, "wb") as f:
    f.write(data)
print("\nwrote %s  (%.2f MB, %d bytes missing)" % (args.out, len(data) / 1e6, missing))
