"""Daemon orchestration and lifecycle.

Wires the components together, owns the threads, installs signal handlers, and
guarantees a clean shutdown that releases the RC override so the flight controller
falls back to its own failsafe behavior.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
from dataclasses import dataclass, replace

from pymavlink import mavutil

from . import __version__
from .controller import Controller
from .logging_conf import configure_logging
from .mavlink_link import MavlinkLink
from .settings import Settings
from .state import ControlState, EventBus, VehicleState
from .udp_server import UdpServer

log = logging.getLogger(__name__)


@dataclass
class _Thread:
    name: str
    target: object
    handle: threading.Thread | None = None


class Daemon:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._stop = threading.Event()
        self._vehicle = VehicleState()
        self._control = ControlState()
        self._events = EventBus()
        self._link = MavlinkLink(settings, self._vehicle, self._events)
        self._controller = Controller(settings, self._link, self._vehicle, self._control, self._events)
        self._udp = UdpServer(settings, self._controller, self._vehicle, self._control, self._events)
        self._threads: list[threading.Thread] = []

    def _spawn(self, name: str, target) -> None:  # noqa: ANN001
        t = threading.Thread(target=target, args=(self._stop,), name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def run(self) -> int:
        if self._s.uses_dev_token:
            log.warning("SESSION_TOKEN is the built-in default; set DRONE_SESSION_TOKEN before flight")

        try:
            self._link.connect()
        except (TimeoutError, OSError) as exc:
            log.error("cannot reach flight controller: %s", exc)
            return 1

        self._link.read_calibration()
        self._link.request_data_streams(hz=10)
        if self._s.pilot_takeover_channels:
            # tighten stick-touch takeover latency: RC_CHANNELS at 20 Hz instead of 10
            self._link.request_message_interval(mavutil.mavlink.MAVLINK_MSG_ID_RC_CHANNELS, 20.0)
        self._udp.bind()

        self._install_signal_handlers()

        self._spawn("mavlink-rx", self._link.rx_loop)
        self._spawn("gcs-heartbeat", self._link.heartbeat_loop)
        self._spawn("udp-rx", self._udp.rx_loop)
        self._spawn("telemetry", self._udp.telemetry_loop)
        self._spawn("cmd-worker", self._controller.run_command_worker)

        log.info("airborne daemon ready")
        try:
            # the control loop runs on the main thread so KeyboardInterrupt lands here
            self._controller.run_control_loop(self._stop)
        except KeyboardInterrupt:  # pragma: no cover
            log.info("interrupted")
            self._stop.set()

        self._shutdown()
        return 0

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):  # noqa: ANN001
            log.info("received signal %s, shutting down", signal.Signals(signum).name)
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:  # pragma: no cover - not on main thread
                pass

    def _shutdown(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        try:
            self._link.release_rc_override()
        except Exception:  # pragma: no cover
            log.debug("failed to release override on shutdown", exc_info=True)
        self._udp.close()
        self._link.close()
        log.info("airborne daemon stopped")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="airborne_daemon",
        description=(
            "Airborne MAVLink control daemon. Listens for control/command packets "
            "and streams telemetry back to whoever sends them, so the ground IP is "
            "auto-discovered and does not need to be configured here. Flags override "
            "the matching DRONE_* environment variables."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--udp-host", metavar="IP", help="UDP bind address (default 0.0.0.0)")
    p.add_argument("--udp-port", type=int, metavar="PORT", help="UDP listen port (default 14650)")
    p.add_argument("--mav-device", metavar="DEV", help="flight-controller device (default /dev/ttyACM0)")
    p.add_argument("--session-token", metavar="TOKEN", help="shared secret; must match the ground bridge")
    p.add_argument("--log-level", metavar="LEVEL", help="DEBUG|INFO|WARNING|ERROR (default INFO)")
    return p


def _overrides_from_args(args: argparse.Namespace) -> dict[str, object]:
    mapping = {
        "udp_host": args.udp_host,
        "udp_port": args.udp_port,
        "mav_device": args.mav_device,
        "session_token": args.session_token,
    }
    return {field: value for field, value in mapping.items() if value is not None}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging(args.log_level)
    try:
        settings = Settings()
        overrides = _overrides_from_args(args)
        if overrides:
            settings = replace(settings, **overrides)  # re-validates via __post_init__
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    return Daemon(settings).run()
