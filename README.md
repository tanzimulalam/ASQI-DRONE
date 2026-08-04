# mtsuissl-airborne — drone-side control daemon

ASQI Lab (Autonomous Systems & Quantum Intelligence Laboratory), Middle Tennessee
State University.

The flight-side half of a browser-based transmitter (sticks + arm/disarm +
telemetry) for a Holybro X500-class quad. Two Jetsons: a **ground** one serves the
GUI, a **drone** one is wired to the Pixhawk 6C over USB and is the hardened
control boundary.

Ground-side code, GUI and operator documentation live in
[`mtsuissl-ground`](https://github.com/mtsuissl/mtsuissl-ground). Start there if
you want to fly it rather than change it.

```
 Browser (ground Jetson screen, or a phone on the same network)
    │  WebSocket JSON
    ▼
 GROUND Jetson ──────────  mtsuissl-ground: app/ (GUI + WS↔UDP relay)
    │  UDP 50 Hz, port 14650
    ▼
 DRONE Jetson  ──────────  airborne_daemon (validation + MAVLink)  [this repo]
    │  MAVLink, USB /dev/ttyACM0
    ▼
 Pixhawk 6C / ArduCopter
```

## Why this shape
- Browsers can't speak UDP and shouldn't hold the session token → the ground
  bridge is the translation + auth layer.
- The **airborne daemon is the source of safety**: it validates every packet,
  refuses overrides unless armed + fresh + in an allowed mode, confirms arm state
  from the vehicle (not from a button press), and runs an **active** link-loss
  failsafe. The Pixhawk is always the source of truth.

## Measured facts about *this* aircraft (read 2026-07-07)
- FC: Holybro Pixhawk 6C on `/dev/ttyACM0`, ArduCopter, quad. Heartbeats confirmed.
- RC cal: ch1 roll 1000/1501/2000 · ch2 pitch 1000/1500/2000 · ch3 thr 1000/1000/2000
  · ch4 yaw 1017/1503/2000 (all non-reversed). The daemon re-reads these at startup.
- Modes on switch: Loiter=`FLTMODE6`, AltHold=`FLTMODE4`, rest Stabilize.
- `RC_OVERRIDE_TIME=3` s, `ARMING_CHECK=1` (all on), `FS_THR_ENABLE=1`,
  **`FS_GCS_ENABLE=0`**, `FENCE_ENABLE=0`.

## ⚠ Safety design choices baked in
1. **Web piloting only in AltHold / Loiter / PosHold.** In Stabilize the throttle
   override is direct motor thrust — the daemon **refuses** to send overrides there.
2. **Self-centering throttle** (release = hold altitude). Release all sticks → hover.
3. **Active failsafe.** If the newest valid control packet ages past
   `CTRL_AGE_FAILSAFE_MS` (600 ms) while armed, the daemon neutralizes the sticks,
   releases the override, and commands **LAND** — it does *not* let ArduPilot sit on
   3 s of stale sticks.
4. **Arm gating** on the airborne side on top of `ARMING_CHECK`: fresh link, FC
   connected, allowed mode, throttle centered. Arm result is confirmed from HEARTBEAT.

## Recommended Pixhawk param changes before flight (you apply these)
- **`SYSID_MYGCS = 250`** (named `MAV_GCS_SYSID` on ArduPilot 4.6+) — **required for
  GUI sticks to work at all.** ArduPilot silently drops `RC_CHANNELS_OVERRIDE` from
  any system id other than this one; the daemon sends from `DRONE_SRC_SYS` (250).
  Arm/mode/takeoff use `COMMAND_LONG` and are accepted from anyone, which is why
  they worked while the sticks didn't. Keeping 250 ≠ 255 also means a Mission
  Planner laptop (255) can stay connected to monitor without its joystick ever
  injecting overrides.
- **Stick-touch takeover (no switch).** The safety pilot takes the aircraft the
  moment they move the transmitter sticks. ArduPilot can't report the physical
  sticks on ch1-4 while they're overridden (RC_CHANNELS echoes the override), so
  the transmitter must **mirror roll/pitch/throttle/yaw onto spare channels** via
  its mixer (e.g. ch9-12; on EdgeTX/OpenTX add mixes ch9←Ail, ch10←Ele, ch11←Thr,
  ch12←Rud). Then run the daemon with `DRONE_PILOT_TAKEOVER_CHANNELS=9,10,11,12`.
  The daemon baselines those channels when it starts piloting and releases the
  override the instant any of them moves > `DRONE_PILOT_TAKEOVER_DZ_US` (50 µs
  default). Takeover **latches**: the GUI sticks stay dead (and the GUI-link
  failsafe won't fire over the pilot's head) until the web operator presses
  RESUME — only do that after the safety pilot centers their sticks and agrees.
  Takeover latency ≈ 50-120 ms (RC_CHANNELS at 20 Hz + one 50 Hz control tick).
- The transmitter **mode switch is a second takeover path**: ch5 is never
  overridden, so flipping to the Stabilize position always works. The daemon sees
  the disallowed mode and releases its override on the next control tick
  (≤ ~1 s, bounded by the FC's 1 Hz heartbeat).
- **Firmware-native alternative (not yet in a stable release):** ArduPilot master
  has `RC_OPTIONS` bit 14 (`CLEAR_OVERRIDES_BY_RC`) — the FC itself drops MAVLink
  overrides on pilot stick input, no mirrored channels needed. It is absent from
  the 4.6/4.7 release branches; revisit when it ships in stable.
- `RC_OVERRIDE_TIME = 3` (already set) — backstop: if the daemon dies silently,
  the transmitter regains stick authority within 3 s.
- Confirm `RC_OPTIONS` bit 1 ("Ignore MAVLink Overrides") is **not** set, or the
  GUI sticks will never work regardless of sysid.
- `FS_GCS_ENABLE = 1` — turn on GCS failsafe so loss of the MAVLink GCS link also
  triggers the FC's own failsafe (independent of our daemon). The daemon now
  heartbeats as the GCS at 1 Hz, so this watches the daemon process itself.
- Consider `FENCE_ENABLE = 1` with a sane radius/altitude for early flights.
- Confirm battery failsafe voltages are set for your pack.

---

# Setup

## Normal operation: it starts itself

Both daemons run under systemd and come up on boot. There is nothing to launch by
hand, and the hotspot starts itself too.

```bash
sudo ./systemd/install.sh          # idempotent, safe to re-run after a pull

systemctl status drone-airborne drone-camera
journalctl -u drone-airborne -f
```

The installer:

* installs `drone-airborne.service` and `drone-camera.service`
* creates `/etc/drone/drone.env` (mode 0640) holding `DRONE_SESSION_TOKEN`, so the
  shared secret is never committed and is not world readable
* installs a NetworkManager dispatcher at
  `/etc/NetworkManager/dispatcher.d/90-drone-hotspot`
* sets the `drone-hotspot` connection to autoconnect

**Why the dispatcher exists.** Regulatory domain and TX power are runtime-only
state. A plain `nmcli con up` does not restore them, so without it the AP silently
comes back on the world-domain fallback (`country 00`, about 14 dBm instead of 20)
after every reboot, costing a large part of the usable range. The dispatcher
re-applies both whenever the hotspot comes up. Check it with
`journalctl -t drone-hotspot`.

**Do not raise TX power above 20 dBm.** This Realtek driver accepts and reports
30 dBm without regard to the regulatory database, which would put the lab outside
FCC limits for 2.4 GHz.

## Running by hand (development)

Both sides are configured entirely through environment variables — no source
edits. Pick one shared secret and export it as the token on both machines.

Each side takes its settings as command-line flags (which override the matching
`DRONE_*` / `GROUND_*` env vars, which override defaults). Use `--help` for the
full list.

Stop the service first, or the two will fight over UDP 14650:
`sudo systemctl stop drone-airborne`

### Drone (airborne) Jetson — this box
```bash
cd ~/Documents/mtsuissl-airborne
python3 -m airborne_daemon --session-token 'a-long-shared-secret'
# other flags: --udp-port, --mav-device, --log-level DEBUG   (see --help)
```
The airborne daemon needs **no peer IP** — it streams telemetry back to whoever
sends it valid control packets. Only `pymavlink` is required (already present).
Run the tests with `python3 -m pytest`.

### Ground Jetson (serves the GUI)
```bash
cd ~/Documents/mtsuissl-ground
pip3 install -r requirements.txt                 # or: pip3 install --user -r requirements.txt
# Pass the drone's IP as the first argument (the token must match the drone):
python3 -m app 10.131.237.193 --token 'a-long-shared-secret'
#   equivalently: --airborne-ip 10.131.237.193 --port 8000
#   (the plain `uvicorn app.main:app` + GROUND_* env vars still works too)
```
Then open **http://<ground-ip>:8000/** on a phone/laptop that can reach the ground
Jetson. Health check: `curl http://<ground-ip>:8000/healthz`.

> **Which drone IP?** For bench testing over the wire, use `10.131.237.193`. For
> flight, use the drone's hotspot IP (e.g. `10.42.0.1`) — see the link section.

> **Copy the ground repo to the ground Jetson:**
> `scp -r ~/Documents/mtsuissl-ground john@10.131.237.193:~/Documents/`

### Architecture
```
CONTROL
browser ─WebSocket(JSON)/8000─▶ ground: FastAPI app (app/) ─UDP(JSON)/14650─▶ drone: airborne_daemon (pkg)
        ◀─ telemetry + boxes ──                              ◀──── telemetry ─────

VIDEO + DETECTION
browser ◀─ /camera/stream.mjpg ─ ground bridge (proxy) ◀─ MJPEG/8090 ─ drone: camera_daemon ◀─ USB webcam
                                                        │
                        ground: detector/ (TensorRT) ◀───┘  boxes ─▶ bridge ─▶ browser (`det` over /ws)
```
The ground bridge injects the session token server-side (never in the browser),
validates every message with pydantic, and fans telemetry to all clients with
per-client backpressure. The airborne daemon re-validates everything and owns the
arm policy, RC scaling, and failsafe. Wire contract: `PROTOCOL.md` (proto v1).

Video and detection are entirely separate from the control path: nothing in them
can stall the 50 Hz uplink, and both can be down while the aircraft still flies.

---

# Video and object detection

Two optional services, one per Jetson. Neither is required to fly.

### Drone: `camera_daemon` (MJPEG streamer, port 8090)

```bash
cd ~/Documents/mtsuissl-airborne
python3 -m camera_daemon          # auto-detects the webcam; --help for flags
```

Auto-detects a working `/dev/video*` by actually requiring frames from it, then
forwards the camera's **own JPEG frames without decoding them**. Check what it
picked with `curl http://<drone-ip>:8090/healthz`:

```json
{"connected":true,"device":"/dev/video0","backend":"gstreamer","passthrough":true,"fps":30.0}
```

`backend` and `passthrough` are the two fields worth reading. `gstreamer` +
`passthrough: true` is the good path — frames come off the camera already encoded
and are never re-encoded.

| Env | Default | Notes |
| --- | --- | --- |
| `CAM_BACKEND` | `auto` | `gstreamer` \| `v4l2` \| `auto`. GStreamer keeps only the newest frame (`appsink drop=true max-buffers=1`) instead of letting stale ones age in V4L2's buffer ring, so it is the lower-latency path. `auto` falls back to V4L2 per device. |
| `CAM_GST_PIPELINE` | — | Full pipeline override; must end in an `appsink`. Escape hatch for odd cameras, or to transcode on the Jetson's JPEG engine. |
| `CAM_DEVICE` | `auto` | Force one, e.g. `/dev/video1`. |
| `CAM_WIDTH` / `CAM_HEIGHT` / `CAM_FPS` | 640/480/30 | Lower `CAM_FPS` first if the link is tight. |
| `CAM_JPEG_QUALITY` | 80 | **Only applies when re-encoding.** With passthrough on (the default) the camera picks the quality and this does nothing. |

> The Orin Nano has **no hardware video encoder** (NVENC was dropped; Orin NX and
> AGX keep it). `nvjpegenc` and the GPU are still there, so JPEG and inference are
> hardware-accelerated, but H.264 would have to be software.

### Ground: `detector/` (TensorRT boxes, port 8091)

```bash
cd ~/Documents/mtsuissl-ground
sudo ./detector/run-container.sh
```

Runs [jetson-inference](https://github.com/dusty-nv/jetson-inference)'s detectNet
in the dusty-nv container, consuming the drone's MJPEG feed and serving boxes as
JSON. The bridge long-polls it and forwards them to browsers over the WebSocket
they already hold, so the browser needs no model and no WebGL — which matters
because **Chromium on the Jetson has no hardware WebGL at all**, and the previous
in-browser TensorFlow.js path took ~54 s to load and then ran at ~1 fps.

Inference is slower than the camera, so the detector always works on the *newest*
frame and drops the backlog; boxes describe the scene now, not several seconds
ago. First run downloads the model (~68 MB) and builds the TensorRT engine —
**several minutes** — both cached on the **host** in the jetson-inference clone's
`data/` dir (mounted into the container, the same layout dusty-nv's own
`docker/run.sh` uses), so the container itself is disposable. The launcher expects
the clone at `~/Documents/jetson-inference`; override with `JETSON_INFERENCE_DIR`.

> Don't launch via the repo's own `docker/run.sh` on this host: it derives the
> image tag from the L4T version (`r36.4.7`), which was never published — only
> `r36.2.0` and `r36.3.0` exist. Our launcher pins `r36.3.0`. It also passes no
> camera device into the container: the detector reads the drone's MJPEG feed
> over the network (`DET_STREAM_URL`), not `/dev/video*`.

| Env | Default | Notes |
| --- | --- | --- |
| `DET_STREAM_URL` | `http://10.42.0.1:8090/stream.mjpg` | Upstream camera feed. |
| `DET_NETWORK` | `ssd-mobilenet-v2` | Any detectNet name, or a custom ONNX path. |
| `DET_THRESHOLD` | `0.5` | Confidence cutoff. |
| `DET_MAX_BOXES` | `20` | Cap per frame. |
| `DET_IMAGE` | `dustynv/jetson-inference:r36.3.0` | Newest published tag; this host is R36.4.7. |

If the detector is not running the bridge retries quietly, the cockpit badge reads
`detector offline`, and video is unaffected. If detections stop mid-flight the
overlay clears within 2 s rather than leaving stale boxes over live video.

---

# The air↔ground link (Wi-Fi)

The drone Jetson runs its **own Wi-Fi hotspot**; the ground Jetson (and/or a
phone) joins it. This is a private two-node network you fully control — no
dependence on any shared/campus Wi-Fi, and it works anywhere in the field.

- **Drone Jetson = the access point.** It has a fixed AP address (NetworkManager's
  shared mode uses `10.42.0.1` by default). That is the IP the ground bridge targets:
  `python3 -m app 10.42.0.1 --token ...`.
- **Ground Jetson = a client** that joins the hotspot SSID. It reaches the GUI on
  its own hotspot-assigned address; open `http://<ground-hotspot-ip>:8000/`.

Do NOT use a shared/campus Wi-Fi for the control link: many such networks enable
"client (AP) isolation", which silently blocks station-to-station traffic even
though the devices appear associated. A private hotspot avoids that entirely.

For **bench testing before the hotspot is set up**, the two Jetsons also share the
**wired** `10.130.0.0/15` net (ground `10.131.237.193`, drone `10.131.70.137`) —
fine for file copy and bench Stages 1–3. A flying drone can't be wired.

The airborne daemon binds `0.0.0.0` and auto-replies to the sender, so only the
ground side needs the drone's IP (its first CLI argument). Setup is **two Jetsons,
one drone**: this airborne Jetson (wired to the Pixhawk) and the ground Jetson.

---

# Staged bring-up  (do these IN ORDER — do not skip)

**Stage 0 — bench, props OFF, battery only for logic (or props physically removed).**
Keep props off through Stage 3.

**Stage 1 — telemetry only.**
Start the daemon + bridge, open the GUI. Confirm the status bar shows live mode,
GPS sats, battery, `CTRL` age < 50 ms, `FC` heartbeat age small. Move sticks — the
daemon will NOT send overrides while disarmed. ✅ when telemetry is stable.

**Stage 2 — arm/disarm, props OFF.**
Press LOITER (or ALT HOLD). Hold-to-arm 2 s. Confirm the banner turns red **ARMED**
only after the vehicle actually arms. Motors should idle (MOT_SPIN_ARM). Press
DISARM. Confirm it disarms. Test failsafe: pull the network cable / kill the bridge
while armed → within ~0.6 s the daemon should command LAND (watch mode in Mission
Planner). ✅ when arm/disarm and failsafe behave.

**Stage 3 — override direction check, props OFF, armed, held down HARD or on a test stand.**
With Mission Planner open, nudge each stick and verify motor/attitude response
direction is correct (roll right → right side reacts, pitch forward → correct, yaw,
throttle up → all motors increase). If any axis is backwards, set the matching env
var on the airborne daemon — `DRONE_INVERT_ROLL`, `DRONE_INVERT_PITCH`,
`DRONE_INVERT_THROTTLE`, `DRONE_INVERT_YAW` (all default `false`) — restart it and
re-test. ✅ when all four axes are correct.

**Stage 4 — props on, first hover, open area, low altitude, hand on a backup TX if available.**
Verify hover holds when sticks are released (self-centering → hold). Test a gentle
translate and return. Test link-loss failsafe once at low altitude. Expand envelope
gradually.

---

# Files
```
mtsuissl-airborne/                drone-side (stdlib + pymavlink; OpenCV only for the camera)
  airborne_daemon/                control daemon — the safety boundary
    settings.py       env-driven config, validated at startup
    modes.py          ArduCopter mode numbers
    rc.py             pure stick→PWM scaling (unit-tested)
    protocol.py       packet parsing/validation + telemetry model
    state.py          thread-safe VehicleState / ControlState / EventBus
    mavlink_link.py   FC connection, RX loop, RC-cal read, send helpers
    controller.py     50 Hz loop + failsafe state machine + arm/disarm
    udp_server.py     UDP ingest + telemetry publisher
    daemon.py         orchestration, signals, graceful shutdown
  camera_daemon/                  MJPEG streamer (optional, port 8090)
    settings.py       CAM_* config
    detect.py         webcam auto-detection; GStreamer and V4L2 open paths
    capture.py        capture thread, freshest-frame publishing, JPEG passthrough
    server.py         multipart/x-mixed-replace HTTP server
    service.py        orchestration, signals, graceful shutdown
  systemd/                        boot-time setup (install.sh installs all of it)
    drone-airborne.service        control daemon unit
    drone-camera.service          camera daemon unit
    90-drone-hotspot              NM dispatcher: restores regdomain + TX power
  tests/              pytest: rc, protocol, failsafe, camera, controller (81 tests)

mtsuissl-ground/                  ground-side FastAPI bridge
  app/
    main.py           FastAPI app: static GUI, /ws, /healthz, lifespan
    settings.py       env-driven config (pydantic-settings)
    protocol.py       pydantic validation of browser messages
    udp_link.py       asyncio UDP link to the drone
    hub.py            WebSocket fan-out with per-client backpressure
    camera.py         reverse proxy for the drone's MJPEG feed (/camera/*)
    detections.py     long-polls the detector, fans boxes out over /ws
  detector/                       TensorRT detection (optional, port 8091)
    settings.py       DET_* config
    mjpeg.py          MJPEG client that always yields the newest frame
    engine.py         jetson-inference detectNet wrapper + box formatting
    state.py          latest-result holder with long-poll support
    server.py         /detections + /healthz
    service.py        orchestration, signals, graceful shutdown
    run-container.sh  launches it in the dusty-nv container
  gui/                React + Vite cockpit (see gui/README.md)
  tests/              pytest: protocol, app integration, detector (30 tests)

PROTOCOL.md                       wire contract (proto v1)
```

## Safety model (why it's shaped this way)
- **Two independent validation layers.** The browser holds no secret; the ground
  bridge injects the token and validates with pydantic; the airborne daemon
  re-validates and is the final authority. Malformed/replayed/out-of-range packets
  are dropped at both layers.
- **Latching failsafe.** Sub-600 ms link gaps hover on neutral sticks and recover.
  Crossing 600 ms while armed latches: neutralize → release override → command
  LAND. A latch only clears on operator **RESUME** or disarm — never silently.
- **Mode gate.** Overrides are refused outside AltHold/Loiter/PosHold, but a lost
  link while armed still trips the failsafe regardless of mode.
- **Minimal deps on the flight node.** The airborne daemon is stdlib + pymavlink;
  heavier web deps live only on the ground station.
- **Clean lifecycle.** SIGINT/SIGTERM release the RC override and join all threads
  so the FC falls back to its own failsafe on daemon exit.
