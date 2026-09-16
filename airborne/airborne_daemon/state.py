"""Thread-safe shared state.

Three collaborators touch shared state concurrently: the MAVLink RX thread writes
:class:`VehicleState`, the UDP RX thread writes :class:`ControlState`, and the
control loop / telemetry thread read immutable snapshots. Each container guards
its fields with a lock and exposes ``snapshot()`` returning a frozen copy so
readers never observe a torn update or hold a lock.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque

from .rc import Sticks

monotonic = time.monotonic


def now_ms() -> int:
    """Wall-clock milliseconds since epoch, for wire timestamps only."""
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
# Vehicle state (written by the MAVLink RX thread)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class VehicleSnapshot:
    connected: bool
    armed: bool
    mode_num: int
    hb_age_ms: int
    gps_fix: int
    sats: int
    batt_v: float
    batt_pct: int
    current_a: float
    rel_alt_m: float
    ground_speed: float
    ekf_ok: bool
    statustext: str
    rc_in: tuple[int, ...]


class VehicleState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connected = False
        self._armed = False
        self._mode_num = -1
        self._last_hb_mono = 0.0
        self._gps_fix = 0
        self._sats = 0
        self._batt_v = 0.0
        self._batt_pct = -1
        self._current_a = 0.0
        self._rel_alt_m = 0.0
        self._ground_speed = 0.0
        self._ekf_ok = False
        self._statustext = ""
        self._rc_in: tuple[int, ...] = ()  # raw receiver PWM from RC_CHANNELS (ch1..chN)

    def update_heartbeat(self, *, armed: bool, mode_num: int) -> None:
        with self._lock:
            self._connected = True
            self._armed = armed
            self._mode_num = mode_num
            self._last_hb_mono = monotonic()

    def update(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, f"_{key}", value)

    def mark_disconnected_if_stale(self, timeout_s: float) -> None:
        with self._lock:
            if self._last_hb_mono and monotonic() - self._last_hb_mono > timeout_s:
                self._connected = False

    def snapshot(self) -> VehicleSnapshot:
        with self._lock:
            hb_age = int((monotonic() - self._last_hb_mono) * 1000) if self._last_hb_mono else -1
            return VehicleSnapshot(
                connected=self._connected,
                armed=self._armed,
                mode_num=self._mode_num,
                hb_age_ms=hb_age,
                gps_fix=self._gps_fix,
                sats=self._sats,
                batt_v=round(self._batt_v, 2),
                batt_pct=self._batt_pct,
                current_a=round(self._current_a, 1),
                rel_alt_m=round(self._rel_alt_m, 2),
                ground_speed=round(self._ground_speed, 2),
                ekf_ok=self._ekf_ok,
                statustext=self._statustext,
                rc_in=self._rc_in,
            )


# --------------------------------------------------------------------------- #
# Control state (written by the UDP RX thread)
# --------------------------------------------------------------------------- #
_STALE_AGE_MS = 10 ** 9  # sentinel age when no control packet has ever arrived


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    sticks: Sticks
    seq: int
    age_ms: int
    ground_addr: tuple[str, int] | None


class ControlState:
    """Latest validated stick command plus link-freshness bookkeeping."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sticks = Sticks()
        self._last_seq = -1
        self._last_valid_mono = 0.0
        self._ground_addr: tuple[str, int] | None = None

    def accept_control(self, seq: int, sticks: Sticks, addr: tuple[str, int]) -> bool:
        """Apply a control packet if its sequence advances. Returns acceptance.

        A small backward window is tolerated as a wrap/reset; large backward jumps
        (stale, duplicated, or reordered datagrams) are rejected.
        """
        with self._lock:
            if seq <= self._last_seq and (self._last_seq - seq) < 1000:
                return False
            self._last_seq = seq
            self._sticks = sticks
            self._last_valid_mono = monotonic()
            self._ground_addr = addr
            return True

    def note_ground(self, addr: tuple[str, int]) -> None:
        with self._lock:
            self._ground_addr = addr

    def age_ms(self) -> int:
        with self._lock:
            if not self._last_valid_mono:
                return _STALE_AGE_MS
            return int((monotonic() - self._last_valid_mono) * 1000)

    def snapshot(self) -> ControlSnapshot:
        with self._lock:
            age = _STALE_AGE_MS if not self._last_valid_mono else int(
                (monotonic() - self._last_valid_mono) * 1000
            )
            return ControlSnapshot(
                sticks=self._sticks,
                seq=self._last_seq,
                age_ms=age,
                ground_addr=self._ground_addr,
            )


# --------------------------------------------------------------------------- #
# Event bus (arm results, failsafe transitions, statustext) surfaced to ground
# --------------------------------------------------------------------------- #
class EventBus:
    def __init__(self, maxlen: int = 32) -> None:
        self._lock = threading.Lock()
        self._events: Deque[dict[str, Any]] = deque(maxlen=maxlen)

    def publish(self, kind: str, msg: str, **extra: Any) -> dict[str, Any]:
        event = {"kind": kind, "msg": msg, "ts": now_ms(), **extra}
        with self._lock:
            self._events.append(event)
        return event

    def recent(self, n: int) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[-n:]
