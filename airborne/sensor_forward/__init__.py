"""Read-only MAVLink forwarder for the drone Jetson.

Reads the flight controller's spare USB interface and sprays what it hears at
one or more UDP destinations: the ground bridge, which turns it into JSON on
/sensors/*, and optionally Mission Planner.

Three deliberate properties:

**It does not fly the aircraft.** The control daemon owns the first USB
interface and is untouched by this. If this process dies, hangs or is killed,
the aircraft notices nothing.

**It is one-way by default.** Mission Planner connects as a ground station and
can arm, change modes and write parameters. ArduPilot accepts those commands
from any system id, so a two-way link means two things can command one aircraft.
Pass --bidirectional when you actually want Mission Planner to configure the
vehicle on the bench, and leave it off when flying.

**It asks for its own stream rates.** ArduPilot keeps stream rates per link, so
raising them here does not change what the control daemon receives on its own
port.
"""

__all__ = ["main"]

from .forwarder import main
