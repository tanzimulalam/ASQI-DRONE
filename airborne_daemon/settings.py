"""Runtime configuration.

Settings load from environment variables (prefix ``DRONE_``) with sane, safety-
biased defaults, and are validated eagerly at construction so misconfiguration
fails fast at startup rather than mid-flight.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from . import modes

_DEV_TOKEN = "CHANGE-ME-drone-test-token"


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key}={raw!r} is not an integer") from exc


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key}={raw!r} is not a number") from exc


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    # --- MAVLink link to the flight controller ---
    mav_device: str = field(default_factory=lambda: _env_str("DRONE_MAV_DEVICE", "/dev/ttyACM0"))
    mav_baud: int = field(default_factory=lambda: _env_int("DRONE_MAV_BAUD", 115200))
    source_system: int = field(default_factory=lambda: _env_int("DRONE_SRC_SYS", 250))
    source_component: int = field(default_factory=lambda: _env_int("DRONE_SRC_COMP", 190))
    heartbeat_timeout_s: float = field(default_factory=lambda: _env_float("DRONE_HB_TIMEOUT_S", 3.0))

    # --- UDP link to the ground bridge ---
    udp_host: str = field(default_factory=lambda: _env_str("DRONE_UDP_HOST", "0.0.0.0"))
    udp_port: int = field(default_factory=lambda: _env_int("DRONE_UDP_PORT", 14650))
    telem_hz: float = field(default_factory=lambda: _env_float("DRONE_TELEM_HZ", 10.0))
    session_token: str = field(default_factory=lambda: _env_str("DRONE_SESSION_TOKEN", _DEV_TOKEN))

    # --- Control loop ---
    control_hz: float = field(default_factory=lambda: _env_float("DRONE_CONTROL_HZ", 50.0))

    # --- Link watchdog (age of newest valid control packet, milliseconds) ---
    ctrl_age_degraded_ms: int = field(default_factory=lambda: _env_int("DRONE_AGE_DEGRADED_MS", 150))
    ctrl_age_stale_ms: int = field(default_factory=lambda: _env_int("DRONE_AGE_STALE_MS", 350))
    ctrl_age_failsafe_ms: int = field(default_factory=lambda: _env_int("DRONE_AGE_FAILSAFE_MS", 600))

    # --- Flight-mode policy ---
    allowed_pilot_modes: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            _env_str("DRONE_ALLOWED_MODES", "ALT_HOLD,LOITER,POSHOLD").split(",")
        )
    )
    default_pilot_mode: str = field(default_factory=lambda: _env_str("DRONE_DEFAULT_MODE", "LOITER"))
    failsafe_action: str = field(default_factory=lambda: _env_str("DRONE_FAILSAFE_ACTION", "LAND"))

    # --- Stick shaping ---
    throttle_center_hold: bool = field(default_factory=lambda: _env_bool("DRONE_THROTTLE_CENTER_HOLD", True))
    invert_roll: bool = field(default_factory=lambda: _env_bool("DRONE_INVERT_ROLL", False))
    invert_pitch: bool = field(default_factory=lambda: _env_bool("DRONE_INVERT_PITCH", False))
    invert_throttle: bool = field(default_factory=lambda: _env_bool("DRONE_INVERT_THROTTLE", False))
    invert_yaw: bool = field(default_factory=lambda: _env_bool("DRONE_INVERT_YAW", False))

    # --- Web takeoff (arm + GUIDED auto-climb, then hand off to the pilot mode) ---
    allow_web_takeoff: bool = field(default_factory=lambda: _env_bool("DRONE_ALLOW_TAKEOFF", True))
    takeoff_mode: str = field(default_factory=lambda: _env_str("DRONE_TAKEOFF_MODE", "GUIDED"))
    takeoff_max_alt_m: float = field(default_factory=lambda: _env_float("DRONE_TAKEOFF_MAX_ALT_M", 30.0))
    takeoff_default_alt_m: float = field(default_factory=lambda: _env_float("DRONE_TAKEOFF_DEFAULT_ALT_M", 5.0))
    # fraction of target altitude that counts as "reached" before handing off to LOITER
    takeoff_reach_frac: float = field(default_factory=lambda: _env_float("DRONE_TAKEOFF_REACH_FRAC", 0.90))

    # --- Arm preconditions (belt-and-suspenders atop ArduPilot ARMING_CHECK) ---
    arm_require_mode_allowed: bool = field(default_factory=lambda: _env_bool("DRONE_ARM_REQ_MODE", True))
    arm_require_throttle_centered: bool = field(default_factory=lambda: _env_bool("DRONE_ARM_REQ_THR", True))
    arm_throttle_center_tol: float = field(default_factory=lambda: _env_float("DRONE_ARM_THR_TOL", 0.10))
    arm_require_fresh_control: bool = field(default_factory=lambda: _env_bool("DRONE_ARM_REQ_FRESH", True))

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.control_hz <= 0 or self.control_hz > 250:
            errors.append(f"control_hz {self.control_hz} out of (0, 250]")
        if self.telem_hz <= 0 or self.telem_hz > 50:
            errors.append(f"telem_hz {self.telem_hz} out of (0, 50]")
        if not (0 < self.ctrl_age_degraded_ms < self.ctrl_age_stale_ms < self.ctrl_age_failsafe_ms):
            errors.append(
                "watchdog thresholds must satisfy 0 < degraded < stale < failsafe "
                f"(got {self.ctrl_age_degraded_ms}/{self.ctrl_age_stale_ms}/{self.ctrl_age_failsafe_ms})"
            )
        if not (1 <= self.udp_port <= 65535):
            errors.append(f"udp_port {self.udp_port} out of range")
        for m in self.allowed_pilot_modes:
            if not modes.is_known_mode(m):
                errors.append(f"unknown allowed mode {m!r}")
        if not modes.is_known_mode(self.default_pilot_mode):
            errors.append(f"unknown default_pilot_mode {self.default_pilot_mode!r}")
        if self.default_pilot_mode not in self.allowed_pilot_modes:
            errors.append("default_pilot_mode must be one of allowed_pilot_modes")
        if not modes.is_known_mode(self.failsafe_action):
            errors.append(f"unknown failsafe_action {self.failsafe_action!r}")
        if not modes.is_known_mode(self.takeoff_mode):
            errors.append(f"unknown takeoff_mode {self.takeoff_mode!r}")
        if not (0.0 < self.takeoff_max_alt_m <= 120.0):
            errors.append(f"takeoff_max_alt_m {self.takeoff_max_alt_m} out of (0, 120]")
        if not (0.0 < self.takeoff_default_alt_m <= self.takeoff_max_alt_m):
            errors.append("takeoff_default_alt_m must be in (0, takeoff_max_alt_m]")
        if not (0.0 < self.takeoff_reach_frac <= 1.0):
            errors.append(f"takeoff_reach_frac {self.takeoff_reach_frac} out of (0, 1]")
        if not (0.0 <= self.arm_throttle_center_tol <= 0.5):
            errors.append(f"arm_throttle_center_tol {self.arm_throttle_center_tol} out of [0, 0.5]")
        if not self.session_token:
            errors.append("session_token must not be empty")
        if errors:
            raise ValueError("invalid configuration:\n  - " + "\n  - ".join(errors))

    @property
    def invert_map(self) -> Mapping[str, bool]:
        return {
            "roll": self.invert_roll,
            "pitch": self.invert_pitch,
            "throttle": self.invert_throttle,
            "yaw": self.invert_yaw,
        }

    @property
    def uses_dev_token(self) -> bool:
        return self.session_token == _DEV_TOKEN
