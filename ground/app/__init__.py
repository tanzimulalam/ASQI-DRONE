"""Ground bridge: FastAPI app relaying between the web GUI and the drone.

    browser <-WebSocket(JSON)-> [this app] <-UDP(JSON)-> airborne daemon

Runs on the ground-side Jetson. It serves the GUI, terminates browser WebSockets,
injects the session token server-side, and relays control/telemetry over UDP.
"""
from __future__ import annotations

__version__ = "1.0.0"

# Must match the airborne daemon's PROTOCOL_VERSION.
PROTOCOL_VERSION = 1
