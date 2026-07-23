# drone-test — web transmitter for Pixhawk 6C / ArduCopter

A browser-based transmitter (sticks + arm/disarm + telemetry) for a Holybro
X500-class quad. Two Jetsons: a **ground** one serves the GUI, a **drone** one is
wired to the Pixhawk 6C over USB and is the hardened control boundary.

```
 Browser (phone/laptop)
    │  WebSocket JSON
    ▼
 GROUND Jetson ──────────  ground/ground_bridge.py  (serves GUI + WS↔UDP relay)
    │  UDP 50 Hz, port 14650
    ▼
 DRONE Jetson  ──────────  airborne/airborne_daemon.py  (validation + MAVLink)
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
- `FS_GCS_ENABLE = 1` — turn on GCS failsafe so loss of the MAVLink GCS link also
  triggers the FC's own failsafe (independent of our daemon).
- Consider `FENCE_ENABLE = 1` with a sane radius/altitude for early flights.
- Confirm battery failsafe voltages are set for your pack.

---

# Setup

Both sides are configured entirely through environment variables — no source
edits. Pick one shared secret and export it as the token on both machines.

Each side takes its settings as command-line flags (which override the matching
`DRONE_*` / `GROUND_*` env vars, which override defaults). Use `--help` for the
full list.

### Drone (airborne) Jetson — this box
```bash
cd ~/Documents/drone-test/airborne
python3 -m airborne_daemon --session-token 'a-long-shared-secret'
# other flags: --udp-port, --mav-device, --log-level DEBUG   (see --help)
```
The airborne daemon needs **no peer IP** — it streams telemetry back to whoever
sends it valid control packets. Only `pymavlink` is required (already present).
Run the tests with `python3 -m pytest`.

### Ground Jetson (serves the GUI)
```bash
cd ~/Documents/drone-test/ground
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

> **Copy the repo to the ground Jetson** (it currently lives on the drone Jetson):
> `scp -r ~/Documents/drone-test john@10.131.237.193:~/Documents/`

### Architecture
```
browser ─WebSocket(JSON)/8000─▶ ground: FastAPI app (app/) ─UDP(JSON)/14650─▶ drone: airborne_daemon (pkg)
        ◀──────telemetry──────                              ◀──────telemetry──────
```
The ground bridge injects the session token server-side (never in the browser),
validates every message with pydantic, and fans telemetry to all clients with
per-client backpressure. The airborne daemon re-validates everything and owns the
arm policy, RC scaling, and failsafe. Wire contract: `PROTOCOL.md` (proto v1).

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
throttle up → all motors increase). If any axis is backwards, flip the matching flag
in `airborne/config.py → INVERT` and re-test. ✅ when all four axes are correct.

**Stage 4 — props on, first hover, open area, low altitude, hand on a backup TX if available.**
Verify hover holds when sticks are released (self-centering → hold). Test a gentle
translate and return. Test link-loss failsafe once at low altitude. Expand envelope
gradually.

---

# Files
```
airborne/                         drone-side control daemon (stdlib + pymavlink only)
  airborne_daemon/
    settings.py       env-driven config, validated at startup
    modes.py          ArduCopter mode numbers
    rc.py             pure stick→PWM scaling (unit-tested)
    protocol.py       packet parsing/validation + telemetry model
    state.py          thread-safe VehicleState / ControlState / EventBus
    mavlink_link.py   FC connection, RX loop, RC-cal read, send helpers
    controller.py     50 Hz loop + failsafe state machine + arm/disarm
    udp_server.py     UDP ingest + telemetry publisher
    daemon.py         orchestration, signals, graceful shutdown
  tests/              pytest: rc, protocol, failsafe (39 tests)

ground/                           ground-side FastAPI bridge
  app/
    main.py           FastAPI app: static GUI, /ws, /healthz, lifespan
    settings.py       env-driven config (pydantic-settings)
    protocol.py       pydantic validation of browser messages
    udp_link.py       asyncio UDP link to the drone
    hub.py            WebSocket fan-out with per-client backpressure
  tests/              pytest: protocol + TestClient integration (14 tests)

gui/index.html                    the web transmitter (dual sticks, hold-to-arm)
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
