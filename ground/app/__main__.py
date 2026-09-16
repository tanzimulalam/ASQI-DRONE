"""Command-line entry point: ``python -m app [AIRBORNE_IP] [options]``.

Parses CLI arguments, maps them onto the ``GROUND_*`` environment variables that
:class:`app.settings.Settings` reads, then launches uvicorn. Precedence is
CLI arg > environment variable > built-in default.

The classic ``uvicorn app.main:app`` invocation still works for anyone who prefers
to configure purely through the environment.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app",
        description="Ground bridge between the web GUI and the airborne daemon. "
        "Flags override the matching GROUND_* environment variables.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "airborne_ip",
        nargs="?",
        metavar="AIRBORNE_IP",
        help="drone-side daemon IP to relay to (e.g. 10.131.237.193 wired, or the hotspot IP)",
    )
    p.add_argument("--airborne-ip", dest="airborne_ip_opt", metavar="IP",
                   help="same as the positional AIRBORNE_IP")
    p.add_argument("--airborne-port", type=int, metavar="PORT", help="drone UDP port (default 14650)")
    p.add_argument("--host", default=None, metavar="IP", help="HTTP/WS bind address (default 0.0.0.0)")
    p.add_argument("--port", type=int, default=None, metavar="PORT", help="HTTP/WS port (default 8000)")
    p.add_argument("--token", metavar="TOKEN", help="shared secret; must match the airborne daemon")
    p.add_argument("--log-level", default="info", metavar="LEVEL", help="uvicorn log level (default info)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    airborne_ip = args.airborne_ip_opt or args.airborne_ip
    if airborne_ip:
        os.environ["GROUND_AIRBORNE_IP"] = airborne_ip
    if args.airborne_port is not None:
        os.environ["GROUND_AIRBORNE_PORT"] = str(args.airborne_port)
    if args.token:
        os.environ["GROUND_SESSION_TOKEN"] = args.token

    host = args.host or os.environ.get("GROUND_HOST", "0.0.0.0")
    port = args.port if args.port is not None else int(os.environ.get("GROUND_PORT", "8000"))

    import uvicorn  # imported late so --help/--version don't require it

    uvicorn.run("app.main:app", host=host, port=port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
