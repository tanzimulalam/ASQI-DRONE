# Ground control station

The operator's guide to the ground half of [ASQI-DRONE](../README.md). The ground
Jetson serves a browser fleet screen and cockpit that fly a Holybro Pixhawk 6C
over Wi-Fi, streams the aircraft's camera, and runs TensorRT object detection on
its own GPU.

The drone side is [`../airborne`](../airborne/README.md). The wire contract between
the two is [`../airborne/PROTOCOL.md`](../airborne/PROTOCOL.md). Read it before
changing anything that crosses the link. The project's status document is
[`HANDOFF.md`](HANDOFF.md).

---

## What runs where

```
  Browser (ground Jetson's own screen, or a phone on the same network)
     |  WebSocket JSON
     v
  GROUND JETSON
     ├─ app/        FastAPI bridge: serves the GUI, relays WS <-> UDP, owns login
     ├─ detector/   TensorRT detectNet in a container, boxes over the telemetry socket
     └─ gui/        React cockpit (built with Vite, served from gui/dist)
     |  UDP 50 Hz, port 14650
     v
  DRONE JETSON  ──  airborne_daemon (validation + MAVLink) + camera_daemon (MJPEG)
     |  MAVLink over USB
     v
  Pixhawk 6C / ArduCopter
```

Video and detection are deliberately **out of band**. Neither can stall the 50 Hz
control path, and both can be dead while the aircraft still flies.

| Port | Where | What |
|------|-------|------|
| 8000 | ground | GUI + WebSocket + fleet API + `/camera` proxy |
| 8091 | ground | detector HTTP (`/detections`, `/healthz`) |
| 8090 | drone | camera MJPEG stream |
| 14650 | drone | control / command / heartbeat ingest (UDP) |

## Hardware

| | |
|---|---|
| Ground | Jetson Orin Nano Super, JetPack 6.2 (R36.4.7) |
| Drone | Jetson Orin Nano Super + Holybro Pixhawk 6C on `/dev/ttyACM0` |
| Camera | Logitech Brio 101 (USB UVC) on the drone |
| Radio | TP-Link Archer T2U PLUS (RTL8821AU) on the drone, running the AP; a USB Wi-Fi adapter on the ground, which the drone hears 11 dB better than the onboard radio |
| Link | Piper hosts SSID `drone-tx`, 2.4 GHz ch 11, and is `10.42.0.1` |

This table is Piper's. Omega's differences are in
[HANDOFF section 13](HANDOFF.md#13-the-fleet-piper-and-omega).

---

## Operating it

Everything starts on boot. There is nothing to launch by hand.

Open **`http://<ground-ip>:8000/`**. On the operator laptop the **ASQI Ground
Station** desktop shortcut tries the campus address, then the hotspot address,
and opens whichever answers.

### The fleet screen

![Fleet screen](../docs/images/fleet-screen.png)

One card per aircraft in `/etc/drone/fleet.json`:

| Badge | Meaning |
|---|---|
| **ONLINE** (green) | The drone Jetson answers. Health is read from its daemon when it first comes online and whenever you press **Check health**. |
| **OFFLINE** (red) | The drone Jetson does not answer. Powered off, or not on a network the ground station can reach. |
| **IN SESSION** (blue) | This aircraft is being flown. Its card shows the live session telemetry instead of probing it. |
| **DAEMON SILENT** (amber) | The Jetson is up but the flight daemon did not answer a health check. |
| **IN BUILD** (violet, dashed) | Listed so the fleet is complete, but has no address yet and cannot be flown. |

**Fly** asks for that aircraft's password and probes only that aircraft, so
flying Omega can never quietly connect to Piper. The aircraft decides whether the
password is right; the ground station holds no list of valid passwords.

A battery reading under 1 V shows as **not connected**: the flight controller is
on USB power with no pack plugged in.

### The cockpit

```
┌─────────────────────────────────────────────────────────────┐
│ < FLEET  DISARMED  AIRCRAFT  MODE  GPS  BATT  ALT  ...  LINK│  <- telemetry band
├───────────┬─────────────────────────────────┬───────────────┤
│  VISION   │                                 │    STATUS     │
│  detector │          camera feed            │  link health  │
│  rate/ms  │      + detection boxes          │  ──────────   │
│  objects  │                                 │    EVENTS     │
├───────────┴─────────────────────────────────┴───────────────┤
│  (stick)      LOITER / ALT HOLD / RTL            (stick)    │
│  throttle     ARM & TAKE OFF / DISARM            pitch      │
│  yaw          STICKS | KEYBOARD                  roll       │
└─────────────────────────────────────────────────────────────┘
```

![Cockpit](../docs/images/cockpit.png)

**Fleet button.** Top left. Ends the session and returns to the fleet screen.
Disabled, and labelled **Armed**, while the aircraft is armed, so it cannot be
pressed by accident in flight.

**Telemetry band.** Every chip reserves its worst-case width so the row never
reflows, and the band cannot wrap. On narrow screens the least critical readings
(current, ground speed, link phase, drone IP) hide rather than letting the row
grow, because a bar that changes height resizes the video underneath it.

**Blue dot on a mode button** means the aircraft accepts stick input in that mode.
Outside those modes the airborne daemon refuses overrides outright, which is
otherwise invisible until you push a stick and nothing happens.

**Arm Only vs Arm & Take Off.** `Arm Only` arms and stops there, motors at
`MOT_SPIN_ARM` idle. `Arm & Take Off` arms *and commands a climb*. Use `Arm Only`
for anything on the ground: with the props off there is no thrust, so the flight
controller keeps raising throttle chasing an altitude it can never reach and winds
unloaded motors toward full RPM.

### Flying

Two control sources, one active at a time. The unused one greys out.

**Sticks** are self-centering on all four axes. Releasing the left stick returns
throttle to centre, which in AltHold / Loiter / PosHold means *hold altitude*.

**Keyboard** is helicopter-sim style:

| Key | Axis | | Key | Axis |
|---|---|---|---|---|
| `W` / `S` | throttle up / down | | `Numpad 8` / `2` | pitch forward / back |
| `A` / `D` | yaw left / right | | `Numpad 4` / `6` | roll left / right |

Arrow keys mirror the numpad for laptops without one. Keys ease in over ~300 ms
and are capped at 60% of full stick (`VITE_KEY_MAX` at build time). Releasing
ramps down faster than pressing ramps up, because returning to neutral should
never be the slow direction.

Losing window focus with a key held zeroes every axis immediately. A `keyup` that
never arrives would otherwise pin an axis at full deflection.

### Banners

| Banner | Meaning |
|---|---|
| **NO CONTROL AUTHORITY** (red) | The aircraft is receiving our packets and discarding them. Usually a token mismatch. Everything else keeps looking healthy, which is exactly why this is a banner. |
| **FAILSAFE ACTIVE** | The airborne daemon latched a failsafe and is landing. Clears only on RESUME or disarm. |
| **PILOT TAKEOVER** | A safety pilot moved the transmitter sticks. GUI sticks stay dead until RESUME. |

---

## Bench testing with the props off

Verified working 2026-08-04: motors idle, sticks drive them differentially.

**Props off. Aircraft secured.** An unloaded quad vibrates and will walk off a
bench.

1. Transmitter **on**, throttle stick fully down (an FS-i6X sits on a warning
   screen and transmits nothing until you do)
2. Press the **hardware safety switch** until its LED goes solid
3. In the GUI: **ALT HOLD**, then **Arm Only**
4. Move the sticks and listen for individual motors changing pitch

### The mode gate catches everyone

Indoors, arming fails with:

```
PreArm: Need Position Estimate
```

That is **not** an arming-check problem and turning checks off will not fix it.
GUIDED, LOITER, POSHOLD and RTL each require a position estimate *by virtue of
being that mode*. `ARMING_CHECK` has no say in it. **ALT_HOLD needs only the
barometer**, which is why it is the mode for bench work. This blocks a transmitter
arm gesture exactly the same way it blocks the GUI button.

### If you must arm without GPS

Only with props off, and put them back the moment you are done:

On the drone Jetson, use `airborne/tools/benchmode.sh`. It stops the daemon
(which owns the serial port), writes `ARMING_CHECK` and `FENCE_ENABLE`, reads
them back from the vehicle, and restarts the daemon on every exit path, including
failure. Its usage line is at the top of the script. **Turning bench mode off
again is not optional**, and it is only done when the read-back says 1 for both.

`FENCE_ENABLE=1` demands a position fix before arming, so an enabled geofence
makes indoor arming impossible on its own. That is the fence working correctly,
not a fault.

### GUI changes not showing up

The bridge serves `gui/dist`. After `npm run build`, **hard reload the browser**
(`Ctrl+Shift+R`). A plain F5 reuses the cached `index.html`, which still points at
the previous JavaScript bundle, so a new build appears to do nothing.

## Range testing

On the ground Jetson, with someone carrying the drone away:

```bash
cd ~/Documents/ASQI-DRONE/ground && ./tools/linkcheck.sh
```

One line per second: RSSI, negotiated 802.11 rates, ping RTT, and bytes actually
flowing from the video stream. It warns below -75 dBm or on any lost packet, and
logs CSV to `/tmp/linkcheck-*.csv`.

Watch the **TX rate**, not the signal. The rate falls first and is the earlier
warning. Detector frame rate in the VISION panel is a useful second indicator:
it drops from about 30 fps wired to about 20 fps over Wi-Fi at close range, and
falls further with distance.

Measured on the bench at 20 dBm, channel 11:

| | |
|---|---|
| Drone to ground | 39.8 Mbit/s |
| Ground to drone | 28.8 Mbit/s |
| Video consumes | 10.1 Mbit/s |

Test outdoors with line of sight, and hold the drone at flight height. Ground
level 2.4 GHz is considerably worse than two metres up.

---

## Services

```bash
systemctl status ground-bridge ground-detector     # ground Jetson
systemctl status drone-airborne drone-camera       # drone Jetson

journalctl -u ground-bridge -f
journalctl -u ground-detector -f
```

Install or reinstall after pulling changes:

```bash
sudo ./systemd/install.sh
```

Idempotent. It writes `/etc/drone/ground.env` on first run only, installs both
units, sets `drone-link` to autoconnect, and enables everything.

The drone side has its own installer in the airborne repo, which also installs a
NetworkManager dispatcher that restores the regulatory domain and TX power every
time the hotspot comes up. Both are runtime-only state that otherwise reverts to
`country 00` and 14 dBm on reboot, costing most of the usable range.

## Configuration

`/etc/drone/ground.env`, read by both ground services. Not in git.

| Variable | Default | Notes |
|---|---|---|
| `GROUND_FLEET_FILE` | `/etc/drone/fleet.json` | The fleet list. Without it the bridge falls back to `GROUND_AIRBORNE_IPS` and health checks are unavailable. |
| `GROUND_AIRBORNE_IPS` | `10.42.0.1,10.131.70.137` | Fallback candidates, tried in order, when there is no fleet file. |
| `DET_STREAM_URL` | `http://10.42.0.1:8090/stream.mjpg` | Where the detector pulls frames. |
| `DET_NETWORK` | `ssd-mobilenet-v2` | detectNet model. Changing it triggers a multi-minute TensorRT rebuild. |
| `DET_THRESHOLD` | `0.5` | Confidence cutoff. |
| `DET_MIN_AVAIL_MB` | `1200` | Memory headroom to wait for before loading the engine. |

Each **aircraft password** is that aircraft's session token. It lives in
`/etc/drone/drone.env` on its drone Jetson (mode 0640, never committed) and the
operator types it at the GUI. The bridge injects it server side, so the browser
never holds it.

### `/etc/drone/fleet.json`

```json
{"drones": [
  {"name": "Piper",  "ip": "10.42.0.1",    "token": "..."},
  {"name": "Omega",  "ip": "10.130.143.5", "token": "..."},
  {"name": "Vulkan", "ready": false}
]}
```

Mode 0640, `root:john`. Tokens here are used only for read-only health probes;
`tests/test_fleet.py` checks they never reach a browser. An aircraft with
`"ready": false` needs no address or token. Restart `ground-bridge` after editing.
A broken file does not stop the bridge: it logs the problem and falls back to
`GROUND_AIRBORNE_IPS`.

## Development

```bash
pip3 install -r requirements-dev.txt
python3 -m pytest                       # all pass except 3 known stale tests (below)

cd gui
npm install
npm run dev                             # hot reload against a running bridge
npm run build                           # writes gui/dist, which the bridge serves
```

After changing anything in `gui/src`, **rebuild**. The bridge serves `gui/dist`,
not the sources. The Jetson's Node is too old for Vite 5, so build on another
machine and copy `gui/dist` across.

**Without a drone.** `tools/sim_drone.py` is a stand-in aircraft that answers the
login and streams steady telemetry, enough to work on the fleet screen and
cockpit anywhere. Instructions are in its docstring and in the
[root README](../README.md#working-on-it).

Three tests in `tests/test_app.py` fail. They predate the login gate and never
send an `auth` message, so nothing is ever forwarded and they time out. They are
stale tests, not product bugs, and are tracked separately.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Cockpit looks alive but nothing responds | Wrong token: aircraft is dropping every packet | The NO CONTROL AUTHORITY banner names this. Check the token matches on both sides. |
| GUI loads, no video, `/camera` returns 502 | Camera proxy is login gated, or `camera_daemon` is down | Log in first. Then `systemctl status drone-camera` on the drone. |
| Detector offline, `:8091` bound but not answering | TensorRT lost a memory race at boot and aborted after the HTTP thread bound the port | `systemctl restart ground-detector`. Close Firefox and the software updater: GNOME plus a browser costs about 1.3 GB on an 8 GB board. |
| `NvMapMemAllocInternalTagged ... error 12` | Out of memory. NvMap cannot swap. | Same as above. `run-container.sh` now waits for headroom first. |
| Link works on the bench, not in the field | Campus Wi-Fi client isolation blocks station to station traffic | Use the drone's own hotspot. Never a shared network. |
| Range much worse than expected | Regulatory domain fell back to `country 00` (14 dBm instead of 20) | `iw reg get`. The dispatcher should prevent this; check `journalctl -t drone-hotspot`. |
| GUI changes do not appear | Stale build, or a cached bundle in the browser | `cd gui && npm run build`, then hard reload (`Ctrl+Shift+R`) |
| `PreArm: Need Position Estimate` indoors | Aircraft is in a mode that requires position | Switch to **ALT HOLD**. Not an arming-check issue; disabling checks will not help |
| Every aircraft shows OFFLINE | The bridge cannot reach port 22 on the drone Jetsons, or `fleet.json` has the wrong addresses | The bridge logs what it loaded on start (`journalctl -u ground-bridge`, line starting `fleet:`). On the hotspot, check the USB Wi-Fi profile is up. |
| Health check refused while nobody is flying | A bridge older than 2026-09-21 kept the session open after the browser left | Update and restart `ground-bridge`. |
| Sticks do nothing, everything else works | `SYSID_MYGCS` does not match the daemon's `DRONE_SRC_SYS` | ArduPilot silently drops overrides from any other system id. Both must be 250 |

## Safety

Read the staged bring-up in [`../airborne/README.md`](../airborne/README.md) and **do not skip stages**.
Props stay off through Stage 3.

Before any flight:

- `FS_GCS_ENABLE = 1` so the flight controller runs its own GCS failsafe,
  independent of our daemon
- `FENCE_ENABLE = 1` with a sane radius and altitude for early flights
- Battery failsafe voltages set for your pack
- Stage 3 override direction check completed on all four axes

The airborne daemon is the source of safety, not this repo. It re-validates every
packet, refuses overrides outside allowed modes, confirms arm state from the
vehicle rather than from a button press, and runs an active link loss failsafe.
Nothing here can override that, and nothing here should try.

**Radio compliance:** 2.4 GHz TX power is capped at 20 dBm to stay inside FCC
limits. The Realtek driver will accept and report 30 dBm without regard to the
regulatory database. Do not raise it.
