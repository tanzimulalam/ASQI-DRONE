#!/usr/bin/env python3
"""Record or watch the aircraft's sensor streams.

The sensor endpoints are gated on a drone session, the same as the cockpit, so
this logs in first and holds that connection open while it reads. Close it and
the sensor sockets stop serving, which is deliberate: no password, no data.

    # watch attitude go past
    python3 sensor_client.py --host 10.131.237.193 --drone Piper --group attitude

    # record everything to a file, one JSON object per line
    python3 sensor_client.py --host 10.131.237.193 --drone Piper \\
        --group all --out flight.jsonl

Groups: attitude, imu, motors, position, power, all.

The password is read from the DRONE_TOKEN environment variable if set, otherwise
it is prompted for, so it never ends up in your shell history.

Needs: pip install websockets
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

try:
    import websockets
except ImportError:                                    # pragma: no cover
    sys.exit("this needs the websockets package: pip install websockets")


async def run(args, password: str) -> int:
    base = f"ws://{args.host}:{args.port}"
    out = open(args.out, "a", buffering=1) if args.out else None
    count = 0
    try:
        # 1. Log in and keep the cockpit socket open: the session lives as long
        #    as a client holds it, and the sensor endpoints check for it.
        async with websockets.connect(f"{base}/ws") as session:
            await session.send(json.dumps({"t": "auth", "password": password,
                                           "drone": args.drone}))
            reply = json.loads(await asyncio.wait_for(session.recv(), timeout=15))
            if reply.get("t") != "auth_ok":
                print("login refused: %s" % reply.get("reason", reply), file=sys.stderr)
                return 1
            print("connected to %s (%s)" % (reply.get("drone") or "aircraft",
                                            reply.get("airborne")), file=sys.stderr)

            # 2. Read the sensor stream until interrupted.
            async with websockets.connect(f"{base}/sensors/{args.group}") as stream:
                while args.limit == 0 or count < args.limit:
                    frame = await stream.recv()
                    count += 1
                    if out:
                        out.write(frame + "\n")
                    else:
                        msg = json.loads(frame)
                        print("%(ts)d  age=%(link_age_ms)sms  %(data)s" % {
                            "ts": msg["ts"], "link_age_ms": msg["link_age_ms"],
                            "data": json.dumps(msg["data"])})
    except KeyboardInterrupt:
        pass
    finally:
        if out:
            out.close()
            print("wrote %d frames to %s" % (count, args.out), file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="10.131.237.193", help="the ground station")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--drone", default="Piper")
    ap.add_argument("--group", default="all",
                    choices=["attitude", "imu", "motors", "position", "power", "all"])
    ap.add_argument("--out", default=None, help="append JSON lines to this file")
    ap.add_argument("--limit", type=int, default=0, help="stop after N frames (0 = forever)")
    args = ap.parse_args()

    password = os.environ.get("DRONE_TOKEN") or getpass.getpass("aircraft password: ")
    try:
        return asyncio.run(run(args, password))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
