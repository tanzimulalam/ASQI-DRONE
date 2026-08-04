"""UDP ingest and telemetry publishing.

Two threads share one socket:

* :meth:`UdpServer.rx_loop` receives datagrams, authenticates the token, parses
  and validates them, and routes control packets to :class:`ControlState` and
  commands to the :class:`Controller`.
* :meth:`UdpServer.telemetry_loop` periodically builds a :class:`Telemetry`
  snapshot and sends it to the last ground address we heard from.

Telemetry is addressed to whoever last sent a *valid* packet, so the drone never
needs the ground station's IP hard-coded.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

from . import PROTOCOL_VERSION
from .modes import mode_name
from .protocol import CommandPacket, ControlPacket, HeartbeatPacket, ProtocolError, Telemetry, parse_packet
from .state import now_ms

if TYPE_CHECKING:
    from .controller import Controller
    from .settings import Settings
    from .state import ControlState, EventBus, VehicleState

log = logging.getLogger(__name__)

_MAX_DATAGRAM = 2048


class UdpServer:
    def __init__(
        self,
        settings: "Settings",
        controller: "Controller",
        vehicle: "VehicleState",
        control: "ControlState",
        events: "EventBus",
    ) -> None:
        self._s = settings
        self._controller = controller
        self._vehicle = vehicle
        self._control = control
        self._events = events
        self._sock: socket.socket | None = None
        self._rejects = 0  # rate-limit noisy logging of bad packets

    def bind(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._s.udp_host, self._s.udp_port))
        sock.settimeout(0.5)
        self._sock = sock
        log.info("UDP listening on %s:%d", self._s.udp_host, self._s.udp_port)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()

    @property
    def _s_sock(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("UdpServer.bind() has not been called")
        return self._sock

    # -- receive ------------------------------------------------------------ #
    def rx_loop(self, stop: threading.Event) -> None:
        log.info("UDP RX loop started")
        while not stop.is_set():
            try:
                data, addr = self._s_sock.recvfrom(_MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                if stop.is_set():
                    break
                log.exception("UDP recv error")
                continue
            self._handle_datagram(data, addr)
        log.info("UDP RX loop stopped")

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            packet, token = parse_packet(data)
        except ProtocolError as exc:
            self._note_reject(f"malformed from {addr}: {exc}")
            return
        if token != self._s.session_token:
            self._note_reject(f"bad token from {addr}")
            return

        if isinstance(packet, ControlPacket):
            if not self._control.accept_control(packet.seq, packet.sticks, addr):
                pass  # stale/duplicate/reordered; silently ignored
        elif isinstance(packet, CommandPacket):
            self._control.note_ground(addr)
            self._controller.submit_command(packet)
        elif isinstance(packet, HeartbeatPacket):
            self._control.note_ground(addr)

    def _note_reject(self, msg: str) -> None:
        self._rejects += 1
        # log the first few and then every 100th to avoid flooding
        if self._rejects <= 5 or self._rejects % 100 == 0:
            log.warning("rejected packet (#%d): %s", self._rejects, msg)

    # -- telemetry ---------------------------------------------------------- #
    def telemetry_loop(self, stop: threading.Event) -> None:
        period = 1.0 / self._s.telem_hz
        log.info("telemetry loop started at %.0f Hz", self._s.telem_hz)
        while not stop.is_set():
            addr = self._control.snapshot().ground_addr
            if addr is not None:
                try:
                    self._s_sock.sendto(self._build_telemetry().to_json(), addr)
                except OSError:
                    log.debug("telemetry send failed", exc_info=True)
            stop.wait(period)
        log.info("telemetry loop stopped")

    def _build_telemetry(self) -> Telemetry:
        veh = self._vehicle.snapshot()
        ctl = self._control.snapshot()
        return Telemetry(
            ts=now_ms(),
            connected=veh.connected,
            armed=veh.armed,
            mode=mode_name(veh.mode_num),
            mode_num=veh.mode_num,
            hb_age_ms=veh.hb_age_ms,
            gps_fix=veh.gps_fix,
            sats=veh.sats,
            batt_v=veh.batt_v,
            batt_pct=veh.batt_pct,
            current_a=veh.current_a,
            alt=veh.rel_alt_m,
            gspeed=veh.ground_speed,
            ekf_ok=veh.ekf_ok,
            ctrl_age_ms=min(ctl.age_ms, 99999),
            link_phase=self._controller.link_phase.value,
            failsafe=self._controller.failsafe_latched,
            pilot_takeover=self._controller.pilot_takeover,
            allowed_modes=list(self._s.allowed_pilot_modes),
            statustext=veh.statustext,
            events=self._events.recent(5),
            proto=PROTOCOL_VERSION,
        )
