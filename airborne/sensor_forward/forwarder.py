"""The forwarding loop itself: flight controller in, UDP out.

    python3 -m sensor_forward --to 10.42.0.254:14551 --to 10.42.0.254:14550

Each --to is a destination for the raw MAVLink stream. The ground bridge listens
on 14551 and serves /sensors/*; Mission Planner conventionally listens on 14550.

Only one program may hold the flight controller's spare interface at a time, so
stop this before running the diagnostic tools in tools/, or run them against a
different port.
"""
from __future__ import annotations

import argparse
import glob
import logging
import signal
import socket
import sys
import threading
import time

from pymavlink import mavutil

log = logging.getLogger("sensor_forward")

# Messages we want and how often, in Hz. Asked for individually with
# SET_MESSAGE_INTERVAL, which is the supported way: REQUEST_DATA_STREAM is
# deprecated and was observed being ignored on this aircraft, leaving only
# heartbeats flowing. Re-asserted periodically, because a flight controller that
# reboots, or a request that is dropped, otherwise leaves the stream silent
# forever.
MESSAGE_RATES = {
    30: 20,     # ATTITUDE
    27: 10,     # RAW_IMU
    116: 10,    # SCALED_IMU2
    129: 10,    # SCALED_IMU3
    241: 5,     # VIBRATION
    36: 10,     # SERVO_OUTPUT_RAW
    65: 10,     # RC_CHANNELS
    33: 5,      # GLOBAL_POSITION_INT
    74: 5,      # VFR_HUD
    173: 5,     # RANGEFINDER
    132: 5,     # DISTANCE_SENSOR
    24: 2,      # GPS_RAW_INT
    1: 2,       # SYS_STATUS
    147: 1,     # BATTERY_STATUS
}

# Kept as a fallback: older firmware honours these even when it ignores
# SET_MESSAGE_INTERVAL, and asking twice costs nothing.
STREAM_RATES = {
    mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS: 10,      # IMUs, vibration
    mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS: 5,   # GPS, power, status
    mavutil.mavlink.MAV_DATA_STREAM_RC_CHANNELS: 10,      # RC in, servo out
    mavutil.mavlink.MAV_DATA_STREAM_POSITION: 5,          # position, altitude
    mavutil.mavlink.MAV_DATA_STREAM_EXTRA1: 20,           # attitude
    mavutil.mavlink.MAV_DATA_STREAM_EXTRA2: 5,            # VFR HUD
    mavutil.mavlink.MAV_DATA_STREAM_EXTRA3: 5,            # rangefinder, battery
}

DEFAULT_PORT_GLOB = "/dev/serial/by-id/usb-Holybro_Pixhawk6C_*-if02"


def find_port(pattern: str) -> str:
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise SystemExit(
            "no flight controller found matching %s. Is it powered and plugged in?" % pattern
        )
    return matches[0]


def parse_destination(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        raise SystemExit("destination must look like host:port, got %r" % text)
    return host, int(port)


class Forwarder:
    def __init__(self, device: str, destinations: list[tuple[str, int]],
                 baud: int = 115200, bidirectional: bool = False) -> None:
        self.device = device
        self.destinations = destinations
        self.baud = baud
        self.bidirectional = bidirectional
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._link = None
        self._sent = 0
        self._received = 0
        self._requested_at = 0.0

    # -- flight controller --------------------------------------------------- #
    def _connect(self):
        log.info("opening %s", self.device)
        link = mavutil.mavlink_connection(self.device, baud=self.baud, source_system=254)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                hb = link.recv_match(type="HEARTBEAT", blocking=True, timeout=2)
            except TypeError:          # pymavlink defect, see gotcha 9
                continue
            if hb and hb.get_srcSystem() and hb.type != mavutil.mavlink.MAV_TYPE_GCS:
                link.target_system = hb.get_srcSystem()
                link.target_component = hb.get_srcComponent() or 1
                log.info("flight controller up: system %d", link.target_system)
                return link
        raise SystemExit("no heartbeat from the flight controller within 30 s")

    def _request_streams(self) -> None:
        for msg_id, hz in MESSAGE_RATES.items():
            self._link.mav.command_long_send(
                self._link.target_system, self._link.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                msg_id, int(1_000_000 / hz), 0, 0, 0, 0, 0,
            )
        for stream, rate in STREAM_RATES.items():
            self._link.mav.request_data_stream_send(
                self._link.target_system, self._link.target_component, stream, rate, 1
            )
        self._requested_at = time.time()
        log.info("asked for %d messages and %d stream groups",
                 len(MESSAGE_RATES), len(STREAM_RATES))

    # -- the loop ------------------------------------------------------------ #
    def run(self) -> None:
        self._link = self._connect()
        self._request_streams()
        if self.bidirectional:
            log.warning("BIDIRECTIONAL: ground stations on these ports can command "
                        "this aircraft. Do not fly like this.")
            threading.Thread(target=self._uplink_loop, daemon=True).start()
        log.info("forwarding to %s", ", ".join("%s:%d" % d for d in self.destinations))

        last_report = time.time()
        while not self._stop.is_set():
            try:
                msg = self._link.recv_match(blocking=True, timeout=1)
            except TypeError:                      # gotcha 9 again
                continue
            except Exception as exc:               # serial hiccup: reopen rather than die
                log.warning("flight controller read failed (%s); reopening", exc)
                time.sleep(1)
                try:
                    self._link = self._connect()
                    self._request_streams()
                except SystemExit:
                    time.sleep(2)
                continue
            if msg is None or msg.get_type() in ("BAD_DATA",):
                continue
            raw = msg.get_msgbuf()
            self._received += 1
            for dest in self.destinations:
                try:
                    self._sock.sendto(raw, dest)
                    self._sent += 1
                except OSError as exc:             # a destination going away is not fatal
                    log.debug("send to %s:%d failed: %s", dest[0], dest[1], exc)
            now = time.time()
            # Re-assert the rates: a request can be dropped, and a flight
            # controller reboot silently resets them. Cheap insurance against a
            # feed that looks alive (heartbeats) but carries no sensors.
            if now - self._requested_at > 10:
                self._request_streams()
            if now - last_report > 60:
                last_report = now
                log.info("forwarded %d messages", self._received)

    def _uplink_loop(self) -> None:
        """Only runs with --bidirectional: pass ground station traffic to the vehicle."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("0.0.0.0", self.destinations[0][1] + 1000))
        listener.settimeout(1.0)
        while not self._stop.is_set():
            try:
                data, _ = listener.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._link.write(data)
            except Exception as exc:               # pragma: no cover
                log.debug("uplink write failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--to", action="append", default=[], metavar="HOST:PORT",
                    help="UDP destination for the raw stream; repeatable")
    ap.add_argument("--device", default=None, help="serial device (default: autodetect)")
    ap.add_argument("--device-glob", default=DEFAULT_PORT_GLOB)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--bidirectional", action="store_true",
                    help="allow ground stations to command the aircraft. Bench use only")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    destinations = [parse_destination(d) for d in (args.to or ["127.0.0.1:14551"])]
    device = args.device or find_port(args.device_glob)

    fwd = Forwarder(device, destinations, baud=args.baud, bidirectional=args.bidirectional)

    def handle(signum, _frame):
        log.info("signal %s, stopping", signum)
        fwd.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle)

    try:
        fwd.run()
    except KeyboardInterrupt:
        pass
    log.info("stopped after forwarding %d messages", fwd._received)
    return 0


if __name__ == "__main__":
    sys.exit(main())
