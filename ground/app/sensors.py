"""Live flight controller sensor data, as JSON over WebSockets.

The aircraft runs a read-only forwarder (``airborne/sensor_forward``) that reads
the flight controller's spare USB interface and sprays raw MAVLink at this
bridge over UDP. Nothing here ever transmits: this module parses what arrives,
keeps the latest value of each thing, and fans it out to subscribers.

Why not extend the existing telemetry? Because that path flies the aircraft. Its
packet is deliberately small and its protocol version is shared with the airborne
daemon, so adding fields means redeploying flight-critical software. Sensor
streaming is a research convenience and gets its own pipe, where a mistake costs
a chart rather than an aircraft.

Endpoints, split by rate so a client never pays for data it did not ask for:

    /sensors/attitude   roll, pitch, yaw and angular rates          20 Hz
    /sensors/imu        both IMUs, plus vibration and clipping      10 Hz
    /sensors/motors     the four motor outputs and RC inputs        10 Hz
    /sensors/position   altitude, rangefinder, speed, GPS            5 Hz
    /sensors/power      battery voltage, current, consumed          2 Hz
    /sensors/all        every group above, merged                    5 Hz

Each frame is one JSON object with a ``group`` and a ``ts`` in milliseconds since
the epoch, so a recording can be lined up against anything else later.

All of it is gated on the same drone session as the cockpit: no login, no data.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

# Rate each endpoint sends at, in Hz. These are send rates, not capture rates:
# the hub always holds the latest value, and each client is served on its own
# clock, so a slow consumer can never back up the others.
GROUP_RATES: dict[str, float] = {
    "attitude": 20.0,
    "imu": 10.0,
    "motors": 10.0,
    "position": 5.0,
    "power": 2.0,
    "all": 5.0,
}

_DEG = 180.0 / math.pi


def _deg(rad: float | None) -> float | None:
    return None if rad is None else round(rad * _DEG, 2)


class SensorHub:
    """Latest sensor values, parsed from the aircraft's MAVLink forward.

    Holds one dict per group and hands out snapshots. Deliberately has no
    transmit path: the socket is receive-only and nothing in here builds a
    MAVLink message.
    """

    def __init__(self) -> None:
        self._groups: dict[str, dict[str, Any]] = {
            "attitude": {}, "imu": {}, "motors": {}, "position": {}, "power": {}
        }
        self._last_msg_at: float | None = None
        self._count = 0
        self._mav = None          # lazily built: pymavlink is optional at import

    # -- ingest -------------------------------------------------------------- #
    def _parser(self):
        if self._mav is None:
            from pymavlink.dialects.v20 import ardupilotmega as dialect
            self._mav = dialect.MAVLink(file=None)
            self._mav.robust_parsing = True
        return self._mav

    def feed(self, data: bytes) -> int:
        """Parse one datagram. Returns how many messages it contained."""
        try:
            messages = self._parser().parse_buffer(data) or []
        except Exception as exc:                      # malformed packet, keep going
            log.debug("sensor parse error: %s", exc)
            return 0
        # Robust parsing turns junk into BAD_DATA, and bytes that happen to look
        # like an unknown message id into UNKNOWN_<n>, rather than raising.
        # Neither is a message: counting them would make a noisy link look busy
        # and would reset the link-age clock on pure garbage.
        good = [m for m in messages
                if m.get_type() != "BAD_DATA" and not m.get_type().startswith("UNKNOWN_")]
        for msg in good:
            try:
                self._absorb(msg)
            except Exception as exc:                  # one odd message must not stop the rest
                log.debug("sensor absorb error on %s: %s", msg.get_type(), exc)
        if good:
            self._last_msg_at = time.time()
            self._count += len(good)
        return len(good)

    def _absorb(self, msg) -> None:
        kind = msg.get_type()
        g = self._groups
        if kind == "ATTITUDE":
            g["attitude"].update(
                roll=_deg(msg.roll), pitch=_deg(msg.pitch),
                yaw=round((math.degrees(msg.yaw) + 360) % 360, 1),
                roll_rate=_deg(msg.rollspeed), pitch_rate=_deg(msg.pitchspeed),
                yaw_rate=_deg(msg.yawspeed),
            )
        elif kind in ("RAW_IMU", "SCALED_IMU2", "SCALED_IMU3"):
            which = {"RAW_IMU": "imu1", "SCALED_IMU2": "imu2", "SCALED_IMU3": "imu3"}[kind]
            g["imu"][which] = {
                # milli-g and milli-rad/s on the wire
                "acc_g": [round(msg.xacc / 1000.0, 3), round(msg.yacc / 1000.0, 3),
                          round(msg.zacc / 1000.0, 3)],
                "gyro_dps": [round(msg.xgyro / 1000.0 * _DEG, 2),
                             round(msg.ygyro / 1000.0 * _DEG, 2),
                             round(msg.zgyro / 1000.0 * _DEG, 2)],
                "mag_mgauss": [msg.xmag, msg.ymag, msg.zmag],
            }
        elif kind == "VIBRATION":
            g["imu"]["vibration"] = {
                "xyz": [round(msg.vibration_x, 2), round(msg.vibration_y, 2),
                        round(msg.vibration_z, 2)],
                "clipping": [msg.clipping_0, msg.clipping_1, msg.clipping_2],
            }
        elif kind == "SERVO_OUTPUT_RAW":
            g["motors"]["outputs"] = [msg.servo1_raw, msg.servo2_raw,
                                      msg.servo3_raw, msg.servo4_raw]
        elif kind == "RC_CHANNELS":
            g["motors"]["rc"] = [msg.chan1_raw, msg.chan2_raw, msg.chan3_raw,
                                 msg.chan4_raw, msg.chan5_raw, msg.chan6_raw]
            g["motors"]["rc_rssi"] = msg.rssi
        elif kind == "GLOBAL_POSITION_INT":
            g["position"].update(alt_rel_m=round(msg.relative_alt / 1000.0, 2),
                                 heading_deg=round(msg.hdg / 100.0, 1) if msg.hdg != 65535 else None)
        elif kind == "VFR_HUD":
            g["position"].update(alt_origin_m=round(msg.alt, 2),
                                 groundspeed_ms=round(msg.groundspeed, 2),
                                 climb_ms=round(msg.climb, 2))
        elif kind in ("RANGEFINDER", "DISTANCE_SENSOR"):
            if kind == "RANGEFINDER":
                g["position"]["rangefinder_m"] = round(msg.distance, 2)
            else:
                g["position"]["rangefinder_m"] = round(msg.current_distance / 100.0, 2)
                g["position"]["rangefinder_orientation"] = msg.orientation
        elif kind == "GPS_RAW_INT":
            g["position"].update(gps_fix=msg.fix_type, gps_sats=msg.satellites_visible,
                                 gps_hdop=round(msg.eph / 100.0, 2) if msg.eph != 65535 else None)
        elif kind == "SYS_STATUS":
            g["power"].update(voltage_v=round(msg.voltage_battery / 1000.0, 2),
                              current_a=round(msg.current_battery / 100.0, 2)
                              if msg.current_battery >= 0 else None,
                              battery_pct=msg.battery_remaining)
        elif kind == "BATTERY_STATUS":
            g["power"]["consumed_mah"] = (msg.current_consumed
                                          if msg.current_consumed != -1 else None)
        elif kind == "HEARTBEAT":
            from pymavlink import mavutil
            # Several things on a MAVLink network emit heartbeats, including
            # ground stations and peripherals, and theirs carry no flight mode.
            # Taking them produced "Mode(0x00000000)" flickering through the
            # stream, so only the autopilot's own heartbeat counts here.
            if (msg.get_srcComponent() != 1
                    or msg.type == mavutil.mavlink.MAV_TYPE_GCS
                    or msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID):
                return
            g["attitude"]["armed"] = bool(
                msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            g["attitude"]["mode"] = mavutil.mode_string_v10(msg)

    # -- read ---------------------------------------------------------------- #
    def snapshot(self, group: str) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        age_ms = None if self._last_msg_at is None else int((time.time() - self._last_msg_at) * 1000)
        if group == "all":
            body = {k: dict(v) for k, v in self._groups.items()}
        else:
            body = dict(self._groups.get(group, {}))
        return {"group": group, "ts": now_ms, "link_age_ms": age_ms, "data": body}

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "messages": self._count,
            "last_message_age_ms": (None if self._last_msg_at is None
                                    else int((time.time() - self._last_msg_at) * 1000)),
            "groups": {k: len(v) for k, v in self._groups.items()},
        }


class _Receiver(asyncio.DatagramProtocol):
    """Receive-only UDP endpoint: it holds no transport and cannot reply.

    Checked by a test that parses this module and fails if any transmit call
    appears anywhere in it.
    """

    def __init__(self, hub: SensorHub) -> None:
        self._hub = hub

    def datagram_received(self, data: bytes, addr) -> None:
        self._hub.feed(data)

    def error_received(self, exc: Exception) -> None:   # pragma: no cover
        log.debug("sensor socket error: %s", exc)


async def start_receiver(hub: SensorHub, host: str, port: int):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Receiver(hub), local_addr=(host, port)
    )
    log.info("sensor stream listening on %s:%d (receive only)", host, port)
    return transport


def add_sensor_routes(app: FastAPI, authorised: Callable[[], bool]) -> None:
    """Mount /sensors/<group> websockets and /sensors/status.

    ``authorised`` decides whether a client may read; the bridge passes a check
    on the drone session, so sensor data is behind the same password as the
    cockpit.
    """

    async def stream(ws: WebSocket, group: str) -> None:
        if not authorised():
            await ws.close(code=1008, reason="no drone session")
            return
        await ws.accept()
        period = 1.0 / GROUP_RATES[group]
        hub: SensorHub = ws.app.state.sensors
        try:
            while True:
                await ws.send_text(json.dumps(hub.snapshot(group)))
                await asyncio.sleep(period)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            pass
        except asyncio.CancelledError:
            raise

    for name in GROUP_RATES:
        def make(group_name: str):
            async def endpoint(websocket: WebSocket) -> None:
                await stream(websocket, group_name)
            return endpoint

        app.add_api_websocket_route(f"/sensors/{name}", make(name))

    @app.get("/sensors/status", include_in_schema=False)
    async def sensor_status() -> dict[str, Any]:
        hub: SensorHub = app.state.sensors
        return {"authorised": authorised(), "rates_hz": GROUP_RATES, **hub.stats}
