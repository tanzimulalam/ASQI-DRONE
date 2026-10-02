#!/usr/bin/env python3
"""Record or watch the aircraft's sensor streams.

The sensor endpoints are gated on a drone session existing, the same as the
camera. If the cockpit is already open in a browser, that session is enough and
this needs no password at all. Only when nothing is logged in does this log in
itself, and then it holds that connection open while it reads.

That order matters: the aircraft sends telemetry to whoever last sent it a valid
packet, so while a cockpit is flying at 50 Hz, a second login probe is answered
and then immediately overtaken, and the login is refused. Attaching to the
existing session sidesteps that entirely.

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


async def already_logged_in(base_http: str) -> bool:
    """Is a drone session already open? Then no password is needed."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base_http}/sensors/status", timeout=5) as resp:
            return bool(json.load(resp).get("authorised"))
    except Exception:
        return False


async def stream_only(base: str, args) -> int:
    """Read sensors using a session somebody else already holds."""
    out = open(args.out, "a", buffering=1) if args.out else None
    count = 0
    try:
        async with websockets.connect(f"{base}/sensors/{args.group}") as stream:
            while args.limit == 0 or count < args.limit:
                frame = await stream.recv()
                count += 1
                if out:
                    out.write(frame + chr(10))
                else:
                    msg = json.loads(frame)
                    print("%d  age=%sms  %s" % (msg["ts"], msg["link_age_ms"],
                                                json.dumps(msg["data"])))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # A closed socket is the normal way this ends: Ctrl+C, the bridge
        # restarting, or the cockpit logging out and taking the session with it.
        print("stream ended: %s" % type(exc).__name__, file=sys.stderr)
    finally:
        if out:
            out.close()
            print("wrote %d frames to %s" % (count, args.out), file=sys.stderr)
    return 0


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
    except Exception as exc:
        print("stream ended: %s" % type(exc).__name__, file=sys.stderr)
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

    base_ws = f"ws://{args.host}:{args.port}"
    base_http = f"http://{args.host}:{args.port}"

    try:
        # Attach to an open cockpit session when there is one. Logging in while
        # somebody is flying does not work, and is not needed.
        if asyncio.run(already_logged_in(base_http)):
            print("a drone session is already open, attaching to it", file=sys.stderr)
            return asyncio.run(stream_only(base_ws, args))

        password = os.environ.get("DRONE_TOKEN") or getpass.getpass("aircraft password: ")
        return asyncio.run(run(args, password))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
