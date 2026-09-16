"""Camera daemon orchestration and CLI entry point."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading

from . import __version__
from .capture import CameraStream
from .server import CameraHTTPServer
from .settings import CameraSettings

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="camera_daemon",
        description=(
            "Auto-detecting webcam MJPEG streamer for the drone-side Jetson. "
            "Serves the ground GUI's video feed. Flags override CAM_* env vars."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--host", metavar="IP", help="HTTP bind address (default 0.0.0.0)")
    p.add_argument("--port", type=int, metavar="PORT", help="HTTP port (default 8090)")
    p.add_argument("--device", metavar="DEV", help="camera device or 'auto' (default auto)")
    p.add_argument("--log-level", default="INFO", metavar="LEVEL", help="default INFO")
    return p


def _apply_overrides(args: argparse.Namespace) -> None:
    if args.host:
        os.environ["CAM_HOST"] = args.host
    if args.port is not None:
        os.environ["CAM_PORT"] = str(args.port)
    if args.device:
        os.environ["CAM_DEVICE"] = args.device


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    _apply_overrides(args)

    try:
        settings = CameraSettings()
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    stream = CameraStream(settings)
    stream.start()

    server = CameraHTTPServer((settings.host, settings.port), stream)
    stop = threading.Event()

    def _handle(signum, _frame):  # noqa: ANN001
        # Do the minimum here and let the main thread run the actual shutdown.
        # Calling server.shutdown() from the handler would deadlock: it blocks until
        # serve_forever() returns, and serve_forever() runs on whichever thread the
        # handler interrupted.
        log.info("received %s, shutting down", signal.Signals(signum).name)
        stop.set()
        # Don't swallow a repeat signal — restore the default disposition so a second
        # Ctrl-C kills the process outright if shutdown itself wedges.
        signal.signal(signum, signal.SIG_DFL)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle)
        except ValueError:  # pragma: no cover - not on main thread
            pass

    # serve_forever() goes on its own thread so the main thread stays free to call
    # shutdown() — the stdlib requires those be different threads.
    server_thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.5},
        name="camera-http", daemon=True,
    )
    server_thread.start()

    log.info(
        "camera daemon ready on http://%s:%d/stream.mjpg (device=%s, %dx%d@%d)",
        settings.host, settings.port, settings.device,
        settings.width, settings.height, settings.fps,
    )
    try:
        # Timed waits: a signal delivered while blocked in wait() is never missed.
        while not stop.wait(0.5):
            pass
    finally:
        server.shutdown()
        server_thread.join(timeout=3.0)
        server.server_close()
        stream.stop()
        log.info("camera daemon stopped")
    return 0
