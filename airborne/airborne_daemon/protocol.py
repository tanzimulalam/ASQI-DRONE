"""Wire protocol: parsing and validation of ground -> air packets, plus the
telemetry model for air -> ground.

All messages are UTF-8 JSON, one object per UDP datagram. Parsing is strict and
total: malformed input raises :class:`ProtocolError` and is dropped by the caller.
Token authentication is intentionally *not* done here (it needs configuration);
the parsed :class:`~Envelope.token` is exposed for the caller to check.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from .rc import Sticks

# Commands the ground may send. Kept as a frozenset for O(1) validation.
KNOWN_COMMANDS: Final[frozenset[str]] = frozenset(
    {"arm", "disarm", "set_mode", "failsafe", "resume", "takeoff"}
)

_AXIS_EPS: Final[float] = 1e-3  # tolerate float round-trip just past the unit range

# Hard ceiling on a requested takeoff altitude at the wire layer. The airborne
# settings apply a lower, configurable operational cap on top of this; this is
# only a structural sanity bound so an absurd value never reaches the FC.
TAKEOFF_ALT_HARD_MAX_M: Final[float] = 120.0


class ProtocolError(ValueError):
    """Raised when an inbound packet is malformed or out of contract."""


@dataclass(frozen=True, slots=True)
class ControlPacket:
    seq: int
    ts: int
    sticks: Sticks


@dataclass(frozen=True, slots=True)
class CommandPacket:
    cmd: str
    mode: str | None = None
    alt: float | None = None  # requested altitude in metres, for cmd == "takeoff"


@dataclass(frozen=True, slots=True)
class HeartbeatPacket:
    pass


Packet = ControlPacket | CommandPacket | HeartbeatPacket


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ProtocolError(msg)


def _as_float(obj: dict[str, Any], key: str) -> float:
    try:
        v = float(obj[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError(f"field {key!r} not a number") from exc
    _require(math.isfinite(v), f"field {key!r} not finite")
    _require(-1.0 - _AXIS_EPS <= v <= 1.0 + _AXIS_EPS, f"field {key!r} out of [-1, 1]")
    # clamp the epsilon slop so downstream never sees > 1.0
    return max(-1.0, min(1.0, v))


def _as_takeoff_alt(value: Any) -> float:
    """Validate a requested takeoff altitude (metres). Total; raises on bad input."""
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("takeoff 'alt' not a number") from exc
    _require(math.isfinite(v), "takeoff 'alt' not finite")
    _require(0.0 < v <= TAKEOFF_ALT_HARD_MAX_M, f"takeoff 'alt' out of (0, {TAKEOFF_ALT_HARD_MAX_M}] m")
    return v


def parse_packet(raw: bytes | str) -> tuple[Packet, str | None]:
    """Parse a raw datagram into a typed packet and its token.

    Returns ``(packet, token)``; ``token`` is whatever the sender supplied (or
    ``None``). Raises :class:`ProtocolError` on any structural problem.
    """
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("not valid JSON") from exc
    _require(isinstance(obj, dict), "top-level JSON must be an object")

    token = obj.get("token")
    _require(token is None or isinstance(token, str), "token must be a string")

    ptype = obj.get("t")
    _require(isinstance(ptype, str), "missing message type 't'")

    if ptype == "ctrl":
        try:
            seq = int(obj["seq"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("ctrl.seq missing or not an int") from exc
        _require(seq >= 0, "ctrl.seq must be >= 0")
        try:
            ts = int(obj.get("ts", 0))
        except (TypeError, ValueError) as exc:
            raise ProtocolError("ctrl.ts not an int") from exc
        sticks = Sticks(
            roll=_as_float(obj, "roll"),
            pitch=_as_float(obj, "pitch"),
            throttle=_as_float(obj, "thr"),
            yaw=_as_float(obj, "yaw"),
        )
        return ControlPacket(seq=seq, ts=ts, sticks=sticks), token

    if ptype == "cmd":
        cmd = obj.get("cmd")
        _require(isinstance(cmd, str) and cmd in KNOWN_COMMANDS, f"unknown cmd {cmd!r}")
        mode = obj.get("mode")
        _require(mode is None or isinstance(mode, str), "cmd.mode must be a string")
        alt = obj.get("alt")
        if cmd == "takeoff":
            _require(alt is not None, "takeoff requires 'alt'")
            alt = _as_takeoff_alt(alt)
        else:
            _require(alt is None, "'alt' is only valid for takeoff")
        return CommandPacket(cmd=cmd, mode=mode, alt=alt), token

    if ptype == "hb":
        return HeartbeatPacket(), token

    raise ProtocolError(f"unknown message type {ptype!r}")


@dataclass(slots=True)
class Telemetry:
    """Air -> ground telemetry snapshot. Serialized with :meth:`to_json`."""

    ts: int
    connected: bool
    armed: bool
    mode: str
    mode_num: int
    hb_age_ms: int
    gps_fix: int
    sats: int
    batt_v: float
    batt_pct: int
    current_a: float
    alt: float
    gspeed: float
    ekf_ok: bool
    ctrl_age_ms: int
    link_phase: str
    failsafe: bool
    pilot_takeover: bool
    allowed_modes: list[str]
    statustext: str
    events: list[dict[str, Any]] = field(default_factory=list)
    t: str = "tlm"
    proto: int = 0  # set by caller to PROTOCOL_VERSION

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode("utf-8")
