"""Detector orchestration and CLI entry point.

Pulls the drone's MJPEG feed, runs detectNet on the newest frame available, and
publishes boxes over HTTP for the ground bridge to relay to browsers. Runs inside
the jetson-inference container; nothing here needs to exist on the host.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time

from . import __version__
from .engine import Detector
from .mjpeg import MjpegSource
from .server import DetectorHTTPServer
from .settings import DetectorSettings
from .state import DetectionState

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="detector",
        description=(
            "TensorRT object detection for the ground station. Consumes the drone's "
            "MJPEG stream and serves detections as JSON. Flags override DET_* env vars."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--stream-url", metavar="URL", help="upstream MJPEG (default drone camera)")
    p.add_argument("--host", metavar="IP", help="HTTP bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, metavar="PORT", help="HTTP port (default 8091)")
    p.add_argument("--network", metavar="NAME", help="detectNet model (default ssd-mobilenet-v2)")
    p.add_argument("--threshold", type=float, metavar="F", help="confidence cutoff (default 0.5)")
    p.add_argument("--log-level", default="INFO", metavar="LEVEL", help="default INFO")
    return p


def _apply_overrides(args: argparse.Namespace) -> None:
    for attr, env in (
        ("stream_url", "DET_STREAM_URL"), ("host", "DET_HOST"), ("port", "DET_PORT"),
        ("network", "DET_NETWORK"), ("threshold", "DET_THRESHOLD"),
    ):
        value = getattr(args, attr, None)
        if value is not None:
            os.environ[env] = str(value)


def _inference_loop(
    source: MjpegSource, detector: Detector, state: DetectionState,
    settings: DetectorSettings, stop: threading.Event,
) -> None:
    """Detect on the newest frame, forever. Never queues: latency beats completeness."""
    last_seq = 0
    frames = 0
    window_start = time.monotonic()
    fps = 0.0
    while not stop.is_set():
        seq, jpeg = source.next_frame(last_seq, timeout=settings.read_timeout_s)
        if jpeg is None or seq == last_seq:
            continue  # nothing new yet; loop and wait again
        last_seq = seq
        t0 = time.monotonic()
        try:
            boxes, width, height = detector.detect(jpeg)
        except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the loop
            log.warning("detection failed on frame %d: %s", seq, exc)
            continue
        infer_ms = (time.monotonic() - t0) * 1000.0

        frames += 1
        elapsed = time.monotonic() - window_start
        if elapsed >= 1.0:
            fps = frames / elapsed
            frames = 0
            window_start = time.monotonic()
        # jpeg rides along for the /frame.jpg diagnostic (annotated on demand only)
        state.publish(boxes, width, height, infer_ms, fps, jpeg=jpeg)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    _apply_overrides(args)

    try:
        settings = DetectorSettings()
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    if not settings.known_network:
        log.warning("network %r is not a built-in name; assuming a custom model path",
                    settings.network)

    state = DetectionState()
    source = MjpegSource(
        settings.stream_url,
        connect_timeout_s=settings.connect_timeout_s,
        retry_interval_s=settings.retry_interval_s,
        read_timeout_s=settings.read_timeout_s,
    )
    detector = Detector(settings.network, settings.threshold, settings.max_boxes)

    server = DetectorHTTPServer((settings.host, settings.port), state)
    stop = threading.Event()

    def _handle(signum, _frame):  # noqa: ANN001
        # Minimum work in the handler; the main thread runs the shutdown. Calling
        # server.shutdown() here would deadlock against serve_forever().
        log.info("received %s, shutting down", signal.Signals(signum).name)
        stop.set()
        signal.signal(signum, signal.SIG_DFL)  # a second signal kills outright

    def _install_graceful_handlers() -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle)
            except ValueError:  # pragma: no cover - not on main thread
                pass

    # NOTE: graceful handlers are NOT installed yet. detector.load() spends minutes
    # inside one C++ call (the TensorRT engine build), and Python-level signal
    # handlers only run between bytecodes — a handler installed now would sit
    # pending until the build finished, making Ctrl-C appear dead the whole time.
    # During load the OS default (terminate immediately) is the graceful option;
    # everything running is a daemon thread and the engine file is only written
    # after a successful build, so an interrupted build leaves nothing stale.

    server_thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.5},
        name="detector-http", daemon=True,
    )
    server_thread.start()
    log.info("detector serving on http://%s:%d/detections", settings.host, settings.port)

    # Serve HTTP before loading the model: engine build can take minutes on first
    # run, and the bridge should see "not ready" rather than a refused connection.
    source.start()
    load_failed = False
    try:
        detector.load()
    except Exception as exc:  # noqa: BLE001
        # A bad model name or a missing CUDA stack won't fix itself on retry, so
        # exit nonzero and let the container's restart policy or the operator deal
        # with it rather than sitting here serving empty results forever.
        log.error("failed to load %s: %s", settings.network, exc)
        state.set_ready(False, f"model load failed: {exc}")
        load_failed = True
        stop.set()
    else:
        _install_graceful_handlers()
        state.set_ready(True)
        worker = threading.Thread(
            target=_inference_loop, args=(source, detector, state, settings, stop),
            name="detector-infer", daemon=True,
        )
        worker.start()

    try:
        while not stop.wait(0.5):
            pass
    finally:
        server.shutdown()
        server_thread.join(timeout=3.0)
        server.server_close()
        source.stop()
        log.info("detector stopped")
    return 1 if load_failed else 0
