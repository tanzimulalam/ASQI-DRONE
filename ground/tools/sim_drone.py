#!/usr/bin/env python3
"""A stand-in aircraft for working on the GUI without a drone.

Speaks just enough of the wire protocol (airborne/PROTOCOL.md) for the ground
bridge to log in, show the fleet screen and open the cockpit:

- answers token-bearing packets on UDP 14650 with a 10 Hz telemetry stream, and
  ignores packets with any other token, exactly as the real daemon does
- accepts TCP connections on port 22, which is how the bridge decides a drone
  Jetson is reachable

It flies nothing and has no MAVLink behind it. Commands (arm, set_mode) are
accepted and ignored, so the telemetry never changes state. It exists for layout,
styling and login work, not for testing flight logic.

Usage, from ground/:

    python tools/sim_drone.py                       # 127.0.0.2, token "sim"
    python tools/sim_drone.py --ip 127.0.0.3 --token other

Then point a bridge at it with a fleet file such as:

    {"drones": [{"name": "Sim", "ip": "127.0.0.2", "token": "sim"}]}

    GROUND_FLEET_FILE=sim-fleet.json GROUND_DETECTOR_ENABLED=false \\
        python -m uvicorn app.main:app --port 8000

Binding port 22 needs root on Linux. Without it the bridge shows the aircraft as
offline, but logging in still works; pass --no-ssh-port to skip it.
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time

ALLOWED_MODES = ["ALT_HOLD", "LOITER", "POSHOLD"]


def telemetry(ctrl_age_ms: int) -> dict:
    return {
        "t": "tlm",
        "proto": 1,
        "ts": int(time.time() * 1000),
        "connected": True,
        "armed": False,
        "mode": "LOITER",
        "mode_num": 5,
        "hb_age_ms": 180,
        "gps_fix": 3,
        "sats": 17,
        "batt_v": 16.42,
        "batt_pct": 94,
        "current_a": 0.8,
        "alt": 0.0,
        "gspeed": 0.0,
        "ekf_ok": True,
        "ctrl_age_ms": ctrl_age_ms,
        "link_phase": "nominal",
        "failsafe": False,
        "pilot_takeover": False,
        "allowed_modes": ALLOWED_MODES,
        "statustext": "",
        "events": [],
    }


def serve_reachability(ip: str) -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((ip, 22))
    except OSError as exc:
        print(f"port 22 unavailable ({exc}); the fleet screen will show this aircraft offline")
        return
    srv.listen(16)

    def loop() -> None:
        while True:
            conn, _ = srv.accept()
            conn.close()

    threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ip", default="127.0.0.2", help="loopback address to bind (default 127.0.0.2)")
    ap.add_argument("--port", type=int, default=14650)
    ap.add_argument("--token", default="sim", help="the password the bridge must send")
    ap.add_argument("--no-ssh-port", action="store_true", help="do not listen on TCP 22")
    args = ap.parse_args()

    if not args.no_ssh_port:
        serve_reachability(args.ip)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.ip, args.port))
    peers: set[tuple[str, int]] = set()
    last_ctrl = [0.0]

    def receive() -> None:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                # Windows reports an ICMP port-unreachable from an earlier send
                # as an error on the next receive. Nothing to do but carry on.
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            if msg.get("token") != args.token:
                continue
            peers.add(addr)
            if msg.get("t") == "ctrl":
                last_ctrl[0] = time.time()

    threading.Thread(target=receive, daemon=True).start()
    print(f"simulated aircraft on {args.ip}:{args.port}, token {args.token!r}. Ctrl+C to stop.")

    while True:
        age = int((time.time() - last_ctrl[0]) * 1000) if last_ctrl[0] else 20
        packet = json.dumps(telemetry(min(age, 60_000))).encode("utf-8")
        for peer in list(peers):
            try:
                sock.sendto(packet, peer)
            except OSError:
                peers.discard(peer)
        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
