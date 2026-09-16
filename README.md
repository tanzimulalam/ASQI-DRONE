# ASQI drone

Browser-based ground control station for a Holybro X500 class quadcopter, built
at MTSU's ASQI Lab (Autonomous Systems and Quantum Intelligence Laboratory).

An operator flies the aircraft from a web page: on-screen sticks or keyboard,
live video, and object detection boxes drawn over the feed. It is not a Mission
Planner replacement. It is a purpose-built cockpit for this one airframe.

This repository consolidates what were previously two separate repositories,
`mtsuissl-ground` and `mtsuissl-airborne`. Both histories are preserved here in
full, so `git log` still reaches every commit made before the move.

## Layout

| Path | Runs on | Contents |
|---|---|---|
| `ground/` | ground Jetson | FastAPI bridge, TensorRT detector, React cockpit |
| `airborne/` | drone Jetson | control daemon, camera daemon, MAVLink tools |

The two halves run on different machines and deploy independently. They live
together here because they are one system and change together, not because
either machine needs both trees.

## Read first

- **`ground/HANDOFF.md`** is the status document: architecture, flight
  controller parameters read off the vehicle, sixteen documented gotchas,
  network measurements, and open research directions. Section 0 is written for
  an AI assistant picking the project up cold.
- **`airborne/PROTOCOL.md`** is the wire contract between the two halves. Read
  it before changing anything that crosses the link.

`HANDOFF.md` tells you not to trust it, including its own parameter table. That
is deliberate. Three of six safety parameters on the flight controller
contradicted the documentation the first time they were read off the vehicle.

It also describes this repository, not the aircraft. The machines run whatever
they last pulled, and those are not the same thing. Check `git log --oneline -1`
on a Jetson before believing anything here about what it is running.

## System

```
Browser (ground Jetson screen, or any host on the same network)
   |  WebSocket JSON
   v
GROUND JETSON
   +- ground/app/       FastAPI: serves the GUI, relays WS <-> UDP, owns login
   +- ground/detector/  TensorRT SSD-MobileNet-V2, boxes over the telemetry socket
   +- ground/gui/       React cockpit (Vite; the bridge serves gui/dist)
   |  UDP 50 Hz -> port 14650
   v
DRONE JETSON
   +- airborne/airborne_daemon   packet validation, failsafe state machine, MAVLink
   +- airborne/camera_daemon     MJPEG on :8090
   |  MAVLink over USB
   v
Pixhawk 6C / ArduCopter
```

| Port | Where | Purpose |
|---|---|---|
| 8000 | ground | GUI, WebSocket, `/camera` proxy |
| 8091 | ground | detector HTTP |
| 8090 | drone | camera MJPEG |
| 14650 | drone | control, command and heartbeat ingest (UDP) |

Object detection runs on the **ground** Jetson. The aircraft carries no
inference load. Video and detection are out of band and neither can stall the
50 Hz control path.

## Safety

The airborne daemon is the safety boundary, not the ground station. It
re-validates every packet, refuses overrides outside allowed modes, confirms arm
state from the vehicle rather than from a button press, and runs an active
link-loss failsafe. Nothing on the ground side can override that.

Arming indoors requires `ARMING_CHECK=0` and `FENCE_ENABLE=0`. **Both must be
restored to 1 before the aircraft goes outside, and the values must be read back
from the vehicle to confirm.** Use `airborne/tools/benchmode.sh`, which stops the
daemon, writes both, reads them back, and restarts the daemon on every exit path
including failure.

Flight controller parameters persist across power cycles. Powering the aircraft
down does not restore them.

## Secrets

Nothing in this repository is a credential. The shared session token lives in
`/etc/drone/drone.env` on the drone and is not committed, by design. Note that
`airborne/airborne_daemon/settings.py` carries a placeholder default that is used
whenever `DRONE_SESSION_TOKEN` is unset, which means an unconfigured machine runs
on a known value. Set it explicitly.
