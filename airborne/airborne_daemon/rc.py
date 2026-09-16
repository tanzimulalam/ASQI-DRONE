"""RC channel calibration and stick-to-PWM scaling.

Pure, side-effect-free, and fully unit-testable. The daemon reads the vehicle's
real calibration at startup and constructs an :class:`RCCalibration`; these
functions turn normalized stick inputs in ``[-1, 1]`` into microsecond PWM values
that match how ArduPilot interprets ``RC_CHANNELS_OVERRIDE``.

Mode-2 channel map: ch1=roll, ch2=pitch, ch3=throttle, ch4=yaw.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Mapping

# Mode-2 axis -> RC channel index.
AXIS_CHANNEL: Final[dict[str, int]] = {"roll": 1, "pitch": 2, "throttle": 3, "yaw": 4}
AXES: Final[tuple[str, ...]] = ("roll", "pitch", "throttle", "yaw")


@dataclass(frozen=True, slots=True)
class ChannelCal:
    """Calibration for one RC channel (microseconds)."""

    min: int
    max: int
    trim: int
    reversed: bool = False

    def __post_init__(self) -> None:
        if not (self.min < self.max):
            raise ValueError(f"channel min {self.min} must be < max {self.max}")
        if not (self.min <= self.trim <= self.max):
            raise ValueError(f"channel trim {self.trim} out of [{self.min}, {self.max}]")


@dataclass(frozen=True, slots=True)
class RCCalibration:
    """Calibration for the four control channels, keyed by channel index (1..4)."""

    channels: Mapping[int, ChannelCal]

    def __post_init__(self) -> None:
        missing = {1, 2, 3, 4} - set(self.channels)
        if missing:
            raise ValueError(f"missing calibration for channels {sorted(missing)}")

    def channel(self, index: int) -> ChannelCal:
        return self.channels[index]


@dataclass(frozen=True, slots=True)
class Sticks:
    """Normalized stick command; each axis in ``[-1, 1]``. Throttle self-centers."""

    roll: float = 0.0
    pitch: float = 0.0
    throttle: float = 0.0
    yaw: float = 0.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.roll, self.pitch, self.throttle, self.yaw)


NEUTRAL: Final[Sticks] = Sticks(0.0, 0.0, 0.0, 0.0)

# Fallback calibration measured from the vehicle on 2026-07-07 (Holybro Pixhawk 6C).
# Used only if the live parameter read fails; the daemon logs loudly when it does.
DEFAULT_FALLBACK_CAL: Final[RCCalibration] = RCCalibration(
    {
        1: ChannelCal(min=1000, max=2000, trim=1501),  # roll
        2: ChannelCal(min=1000, max=2000, trim=1500),  # pitch
        3: ChannelCal(min=1000, max=2000, trim=1000),  # throttle
        4: ChannelCal(min=1017, max=2000, trim=1503),  # yaw
    }
)


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def scale_axis(value: float, cal: ChannelCal, *, center_hold: bool = False) -> int:
    """Scale a normalized axis value to a PWM microsecond value.

    ``value`` is clamped to ``[-1, 1]`` and non-finite inputs are treated as 0.
    With ``center_hold`` (throttle in altitude-hold modes) the zero point is the
    channel midpoint so releasing the stick commands "hold altitude"; otherwise
    the zero point is the channel trim.
    """
    if not math.isfinite(value):
        value = 0.0
    v = _clamp(value, -1.0, 1.0)
    if cal.reversed:
        v = -v

    lo, hi, trim = cal.min, cal.max, cal.trim
    pivot = (lo + hi) / 2.0 if center_hold else float(trim)
    span = (hi - pivot) if v >= 0.0 else (pivot - lo)
    pwm = pivot + v * span
    return int(round(_clamp(pwm, lo, hi)))


def sticks_to_pwm(
    sticks: Sticks,
    calibration: RCCalibration,
    *,
    invert: Mapping[str, bool] | None = None,
    throttle_center_hold: bool = True,
) -> tuple[int, int, int, int]:
    """Convert :class:`Sticks` to ``(ch1, ch2, ch3, ch4)`` PWM values.

    ``invert`` flips an axis before scaling (used to correct stick direction that
    was verified props-off). Returns channels in ch1..ch4 order (roll, pitch,
    throttle, yaw).
    """
    inv = invert or {}
    values = {
        "roll": sticks.roll,
        "pitch": sticks.pitch,
        "throttle": sticks.throttle,
        "yaw": sticks.yaw,
    }
    out: list[int] = []
    for axis in AXES:
        v = values[axis]
        if inv.get(axis, False):
            v = -v
        ch = AXIS_CHANNEL[axis]
        out.append(
            scale_axis(v, calibration.channel(ch), center_hold=(axis == "throttle" and throttle_center_hold))
        )
    # AXES order is roll, pitch, throttle, yaw == ch1..ch4
    return out[0], out[1], out[2], out[3]


def neutral_pwm(
    calibration: RCCalibration, *, throttle_center_hold: bool = True
) -> tuple[int, int, int, int]:
    """PWM for centered sticks (throttle at the altitude-hold midpoint)."""
    return sticks_to_pwm(NEUTRAL, calibration, throttle_center_hold=throttle_center_hold)
