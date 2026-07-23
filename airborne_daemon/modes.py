"""ArduCopter flight-mode numbers and helpers.

Source: ArduPilot Copter ``Mode::Number`` enumeration. Only the modes this daemon
references are named; the numeric map is authoritative for MAVLink ``custom_mode``.
"""
from __future__ import annotations

from typing import Final

MODE: Final[dict[str, int]] = {
    "STABILIZE": 0, "ACRO": 1, "ALT_HOLD": 2, "AUTO": 3, "GUIDED": 4,
    "LOITER": 5, "RTL": 6, "CIRCLE": 7, "LAND": 9, "DRIFT": 11, "SPORT": 13,
    "FLIP": 14, "AUTOTUNE": 15, "POSHOLD": 16, "BRAKE": 17, "THROW": 18,
    "GUIDED_NOGPS": 20, "SMART_RTL": 21, "FLOWHOLD": 22, "FOLLOW": 23,
    "ZIGZAG": 24, "SYSTEMID": 25, "AUTOROTATE": 26, "AUTO_RTL": 27,
}

MODE_NAME: Final[dict[int, str]] = {v: k for k, v in MODE.items()}


def mode_number(name: str) -> int:
    """Return the numeric mode for ``name`` or raise ``KeyError``."""
    return MODE[name]


def mode_name(number: int) -> str:
    """Return the mode name for ``number`` or a synthetic ``MODE_<n>`` label."""
    return MODE_NAME.get(number, f"MODE_{number}")


def is_known_mode(name: str) -> bool:
    return name in MODE
