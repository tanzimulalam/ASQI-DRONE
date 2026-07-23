"""MAVLink connection to the flight controller.

Wraps a :mod:`pymavlink` connection with a thread-safe send path and an RX loop
that folds incoming messages into :class:`~.state.VehicleState`. ``pymavlink`` is
not safe for concurrent sends, so every transmit is serialized under a lock;
receiving runs in its own thread and only reads.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from pymavlink import mavutil

from . import rc
from .modes import mode_name

if TYPE_CHECKING:
    from .settings import Settings
    from .state import EventBus, VehicleState

log = logging.getLogger(__name__)

_RC_PARAMS = tuple(f"RC{ch}_{k}" for ch in (1, 2, 3, 4) for k in ("MIN", "MAX", "TRIM", "REVERSED"))


class MavlinkLink:
    def __init__(self, settings: "Settings", vehicle: "VehicleState", events: "EventBus") -> None:
        self._s = settings
        self._vehicle = vehicle
        self._events = events
        self._send_lock = threading.Lock()
        self._conn: mavutil.mavfile | None = None
        self.calibration: rc.RCCalibration = rc.DEFAULT_FALLBACK_CAL

    # -- lifecycle ---------------------------------------------------------- #
    def connect(self, *, first_heartbeat_timeout: float = 15.0) -> None:
        log.info("connecting to flight controller on %s", self._s.mav_device)
        self._conn = mavutil.mavlink_connection(
            self._s.mav_device,
            baud=self._s.mav_baud,
            source_system=self._s.source_system,
            source_component=self._s.source_component,
        )
        hb = self._conn.wait_heartbeat(timeout=first_heartbeat_timeout)
        if hb is None:
            raise TimeoutError("no heartbeat from flight controller")
        log.info(
            "flight controller up: sys=%d comp=%d",
            self._conn.target_system,
            self._conn.target_component,
        )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - best-effort on shutdown
                log.debug("error closing MAVLink connection", exc_info=True)

    @property
    def _c(self) -> mavutil.mavfile:
        if self._conn is None:
            raise RuntimeError("MavlinkLink.connect() has not been called")
        return self._conn

    # -- calibration -------------------------------------------------------- #
    def read_calibration(self, *, timeout: float = 8.0) -> bool:
        """Read RC1..4 MIN/MAX/TRIM/REVERSED. Returns True if all were read.

        On partial failure the fallback calibration is retained and a warning is
        logged; the daemon still runs but scaling may be imperfect.
        """
        got: dict[str, float] = {}
        deadline = time.monotonic() + timeout
        last_req = 0.0
        while time.monotonic() < deadline and len(got) < len(_RC_PARAMS):
            if time.monotonic() - last_req > 2.5:
                with self._send_lock:
                    for name in _RC_PARAMS:
                        if name not in got:
                            self._c.mav.param_request_read_send(
                                self._c.target_system, self._c.target_component, name.encode(), -1
                            )
                last_req = time.monotonic()
            msg = self._c.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.2)
            if msg and msg.param_id in _RC_PARAMS and msg.param_id not in got:
                got[msg.param_id] = msg.param_value

        if len(got) < len(_RC_PARAMS):
            missing = [p for p in _RC_PARAMS if p not in got]
            log.warning("RC calibration incomplete, using fallback; missing=%s", missing)
            return False

        channels: dict[int, rc.ChannelCal] = {}
        for ch in (1, 2, 3, 4):
            channels[ch] = rc.ChannelCal(
                min=int(round(got[f"RC{ch}_MIN"])),
                max=int(round(got[f"RC{ch}_MAX"])),
                trim=int(round(got[f"RC{ch}_TRIM"])),
                reversed=bool(round(got[f"RC{ch}_REVERSED"])),
            )
        self.calibration = rc.RCCalibration(channels)
        log.info("RC calibration read from vehicle: %s", channels)
        return True

    def request_data_streams(self, hz: int = 10) -> None:
        with self._send_lock:
            self._c.mav.request_data_stream_send(
                self._c.target_system,
                self._c.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                hz,
                1,
            )

    # -- transmit (all serialized) ----------------------------------------- #
    def send_rc_override(self, ch1: int, ch2: int, ch3: int, ch4: int) -> None:
        with self._send_lock:
            self._c.mav.rc_channels_override_send(
                self._c.target_system, self._c.target_component,
                ch1, ch2, ch3, ch4, 0, 0, 0, 0,  # ch5..8 = 0 -> no override
            )

    def release_rc_override(self) -> None:
        with self._send_lock:
            self._c.mav.rc_channels_override_send(
                self._c.target_system, self._c.target_component,
                0, 0, 0, 0, 0, 0, 0, 0,
            )

    def send_arm(self, arm: bool, *, force: bool = False) -> None:
        with self._send_lock:
            self._c.mav.command_long_send(
                self._c.target_system, self._c.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                1 if arm else 0, 21196 if force else 0, 0, 0, 0, 0, 0,
            )

    def takeoff(self, alt_m: float) -> None:
        """Command an automatic takeoff to ``alt_m`` (relative). Requires the
        vehicle to already be armed and in an auto-takeoff-capable mode (GUIDED)."""
        with self._send_lock:
            self._c.mav.command_long_send(
                self._c.target_system, self._c.target_component,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0,
                0, 0, 0, 0, 0, 0, float(alt_m),  # param7 = altitude
            )

    def set_mode(self, mode_num: int) -> None:
        with self._send_lock:
            self._c.mav.command_long_send(
                self._c.target_system, self._c.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                mode_num, 0, 0, 0, 0, 0,
            )

    # -- receive loop ------------------------------------------------------- #
    def rx_loop(self, stop: threading.Event) -> None:
        log.info("MAVLink RX loop started")
        while not stop.is_set():
            try:
                msg = self._c.recv_match(blocking=True, timeout=1.0)
            except Exception:  # pragma: no cover - transport hiccup
                log.exception("error reading MAVLink; retrying")
                time.sleep(0.1)
                continue
            if msg is None:
                self._vehicle.mark_disconnected_if_stale(self._s.heartbeat_timeout_s)
                continue
            self._dispatch(msg)
        log.info("MAVLink RX loop stopped")

    def _dispatch(self, msg) -> None:  # noqa: ANN001 - pymavlink message type
        mtype = msg.get_type()
        if mtype == "HEARTBEAT" and msg.get_srcComponent() in (0, 1):
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            self._vehicle.update_heartbeat(armed=armed, mode_num=msg.custom_mode)
        elif mtype == "SYS_STATUS":
            self._vehicle.update(
                batt_v=msg.voltage_battery / 1000.0,
                current_a=(msg.current_battery / 100.0 if msg.current_battery != -1 else 0.0),
                batt_pct=msg.battery_remaining,
            )
        elif mtype == "GPS_RAW_INT":
            self._vehicle.update(gps_fix=msg.fix_type, sats=msg.satellites_visible)
        elif mtype == "GLOBAL_POSITION_INT":
            self._vehicle.update(rel_alt_m=msg.relative_alt / 1000.0)
        elif mtype == "VFR_HUD":
            self._vehicle.update(ground_speed=msg.groundspeed)
        elif mtype == "EKF_STATUS_REPORT":
            need = (
                mavutil.mavlink.EKF_ATTITUDE
                | mavutil.mavlink.EKF_VELOCITY_HORIZ
                | mavutil.mavlink.EKF_POS_HORIZ_REL
            )
            self._vehicle.update(ekf_ok=(msg.flags & need) == need)
        elif mtype == "STATUSTEXT":
            text = msg.text.decode() if isinstance(msg.text, (bytes, bytearray)) else msg.text
            self._vehicle.update(statustext=text)
            self._events.publish("statustext", text, severity=int(msg.severity))
            log.info("FC statustext[%d]: %s", msg.severity, text)
