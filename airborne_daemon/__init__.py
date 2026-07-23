"""Airborne control daemon for a Pixhawk 6C / ArduCopter quad.

Runs on the drone-side Jetson, wired to the flight controller over USB. It is the
hardened boundary between the ground station and the vehicle: it validates every
inbound packet, converts sticks to RC overrides, enforces a mode/arm policy, and
runs an active link-loss failsafe. The Pixhawk remains the source of truth for
armed state and mode.

Dependency policy: this package uses only the Python standard library and
``pymavlink``. Nothing heavier is installed on the flight-critical node.
"""
from __future__ import annotations

__all__ = ["__version__", "PROTOCOL_VERSION"]

__version__ = "1.0.0"

# Wire-contract version shared with the ground bridge. Bump on breaking changes.
PROTOCOL_VERSION = 1
