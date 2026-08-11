# Project handoff

Status document for someone picking this project up cold. Written 2026-08-05,
updated 2026-08-06 after the first flight.

Everything below was verified against the running hardware on 2026-08-04/05, not
copied from older documentation. Where this contradicts a README, trust this file
and then go re-read the hardware yourself, because that is exactly how the
earlier discrepancies were found.

---

## 1. What this is

A browser-based ground control station for a Holybro X500-class quadcopter, built
at MTSU's ASQI Lab (Autonomous Systems & Quantum Intelligence Laboratory,
previously named ISSL, which is why the GitHub org is still `mtsuissl`).

An operator flies the aircraft from a web page: on-screen sticks or keyboard,
live video, and object detection boxes drawn over the feed. It is not a Mission
Planner replacement. It is a purpose-built cockpit for this one airframe.

The current owner inherited it with no handover from the previous developer, and
is not the original author of any of the code.

---

## 2. Hardware

| Role | Device | Notes |
|---|---|---|
| Ground station | Jetson Orin Nano Super, JetPack 6.2 (R36.4.7) | runs a full GNOME desktop |
| Drone computer | Jetson Orin Nano Super, same image | |
| Flight controller | Holybro Pixhawk 6C | USB CDC on `/dev/ttyACM0` |
| Camera | Logitech Brio 101 (USB UVC) | on the drone Jetson |
| Drone radio | TP-Link Archer T2U PLUS (RTL8821AU) | USB, hosts the AP |
| Ground radio | Jetson onboard `rtl88x2ce` | client only |
| Transmitter | FlySky FS-i6X | 10 ch radio, but see gotcha 6 |

Airframe class is X500. Battery is 3300 mAh, thresholds configured for **4S**.
Nobody has physically confirmed the cell count. Verify before trusting any
battery failsafe number.

---

## 3. Network

```
Wired campus net (bench):   ground 10.131.237.193   drone 10.131.70.137
Drone hotspot (flight):     SSID drone-tx, 2.4 GHz ch 11, drone is 10.42.0.1
```

The drone hosts the access point; the ground station joins it. This is
deliberate. Campus Wi-Fi has client isolation, which silently blocks
station-to-station traffic, so a shared network cannot work.

The ground bridge tries `10.42.0.1` then `10.131.70.137`, so the same config
works on the bench and in the field.

**Measured over the hotspot at 20 dBm, bench distance:**

| | |
|---|---|
| Drone to ground | 39.8 Mbit/s |
| Ground to drone | 28.8 Mbit/s |
| Video consumes | 10.1 Mbit/s |
| Signal | about -40 dBm |

Detector frame rate drops from roughly 30 fps wired to 20 fps over Wi-Fi at close
range. It is frame starvation, not GPU load: inference stays at 18-22 ms.

**A 50 m range test has never been run.** It is the one original goal still
outstanding. `tools/linkcheck.sh` exists for it.

---

## 4. Access

SSH as user `john` on both Jetsons. Both have `gh` 2.4.0 already authenticated as
`mtsuissl`, with a credential helper in `~/.gitconfig`, so **plain `git pull`
works on both machines with no setup**.

Credentials are deliberately not in this file. The lab SSH password rotates
weekly; a persistent key was installed to avoid depending on it.

- Drone session token: `/etc/drone/drone.env` on the drone Jetson, mode 0640
- Ground config: `/etc/drone/ground.env`

Neither file is in git, by design.

---

## 5. Repositories

| Repo | Runs on | Contents |
|---|---|---|
| `mtsuissl/mtsuissl-ground` | ground Jetson | FastAPI bridge, TensorRT detector, React cockpit |
| `mtsuissl/mtsuissl-airborne` | drone Jetson | control daemon, camera daemon, MAVLink tools |

Clone paths are `~/Documents/mtsuissl-ground` and `~/Documents/mtsuissl-airborne`
respectively, one per machine.

`PROTOCOL.md` in the airborne repo is the wire contract between them. Read it
before changing anything that crosses the link.

> **`mtsuissl-ground` had zero commits until 2026-08-04.** The entire ground
> codebase existed only on one SD card. This is worth knowing because it means
> git history starts mid-project and tells you nothing about how the design
> evolved.

---

## 6. Architecture

```
Browser (ground Jetson screen, or a phone on the same network)
   |  WebSocket JSON
   v
GROUND JETSON
   +- app/       FastAPI: serves GUI, relays WS <-> UDP, owns login
   +- detector/  TensorRT detectNet in a container, boxes over the telemetry socket
   +- gui/       React cockpit (Vite; the bridge serves gui/dist, NOT the sources)
   |  UDP 50 Hz -> port 14650
   v
DRONE JETSON
   +- airborne_daemon   packet validation, failsafe state machine, MAVLink
   +- camera_daemon     MJPEG on :8090
   |  MAVLink over USB
   v
Pixhawk 6C / ArduCopter
```

| Port | Where | Purpose |
|---|---|---|
| 8000 | ground | GUI, WebSocket, `/camera` proxy |
| 8091 | ground | detector HTTP |
| 8090 | drone | camera MJPEG |
| 14650 | drone | control/command/heartbeat ingest (UDP) |

Two design points that matter:

**Object detection runs on the GROUND Jetson, not the drone.** The detector pulls
the drone's MJPEG stream and runs SSD-MobileNet-V2 on TensorRT. The aircraft
carries no inference load at all. Any question of the form "will detection slow
the drone down" is already answered: no.

**Video and detection are out of band.** Neither can stall the 50 Hz control
path, and both can be dead while the aircraft still flies.

---

## 7. Current state

### Working and verified on hardware

- All four services start on boot and survive a **full power-off cold boot**
  (verified 2026-08-05, both machines, nothing started by hand)
- Hotspot self-starts; ground station rejoins it unaided
- Regulatory domain and TX power restored on every hotspot bring-up by a
  NetworkManager dispatcher
- Detector: SSD-MobileNet-V2, TensorRT FP16, 18-22 ms, roughly 30 fps
- GUI: fluid 800x480 to 4K, MTSU branding, live detection and status panels
- Keyboard piloting (WASD + numpad), eased ramp, 60% deflection cap, and an
  interlock that zeroes all axes on window focus loss
- Bench arming with props off: motors respond to both stick and keyboard input

### First flight, 2026-08-06

The aircraft flew. GPS locked for the first time (16 satellites, EKF healthy) and
it held position in Loiter under GUI control.

Two things were learned the hard way and both are recorded below: the pitch axis
was reversed (found in the air, fixed with `DRONE_INVERT_PITCH`), and the control
link failsafe fired for real at altitude. See section 8a.

### Still never done

- **50 m range test.** `tools/linkcheck.sh` exists for it.
- **A full Stage 3 direction check.** Pitch was found reversed *while flying*
  rather than on the bench. Roll, yaw and throttle have still never been verified
  props-off. Do this before the next flight.
- **Stage 2 link-loss test on the bench.** It has now happened involuntarily in
  the air, which is not the same as testing it deliberately.

### Test suites

| Suite | Result |
|---|---|
| airborne | 81 pass |
| ground | 47 pass, **3 fail** |

The 3 failures are in `tests/test_app.py`. They predate the login gate, never
send an `auth` message, and time out waiting for datagrams that correctly never
arrive. They are stale tests, not product bugs.

---

## 8. Flight controller configuration

Read off the vehicle 2026-08-05. **Re-read before trusting.**

| Parameter | Value | Meaning |
|---|---|---|
| `SYSID_MYGCS` | 250 | must match the daemon's `DRONE_SRC_SYS` |
| `ARMING_CHECK` | 1 | all pre-arm checks on |
| `FS_GCS_ENABLE` | 1 | independent GCS failsafe |
| `FENCE_ENABLE` | 1 | type 7, action RTL, 300 m radius, 100 m ceiling |
| `FS_THR_ENABLE` | 1 | throttle failsafe |
| `RC_OVERRIDE_TIME` | 3 | transmitter regains authority 3 s after daemon death |
| `RC_OPTIONS` | 32 | bit 1 (ignore MAVLink overrides) is clear, as required |
| `BATT_MONITOR` | 4 | voltage and current |
| `BATT_CAPACITY` | 3300 | mAh |
| `BATT_ARM_VOLT` | 14.0 | refuses to arm below this |
| `BATT_LOW_VOLT` / `BATT_FS_LOW_ACT` | 14.0 / 2 | RTL |
| `BATT_CRT_VOLT` / `BATT_FS_CRT_ACT` | 13.6 / 1 | Land |
| `RNGFND1_TYPE` | 0 | no rangefinder |
| `SERIAL5` / `SERIAL6` | -1 | free, if a sensor is ever added |

Use `tools/params.py` in the airborne repo to read and write these. It confirms
every write by reading it back from the vehicle.

> **Firmware naming quirk:** this board uses `RNGFND1_MIN_CM` / `RNGFND1_MAX_CM`
> (centimetres). Current ArduPilot documentation uses `RNGFND1_MIN` / `_MAX` in
> metres. Following the docs literally will set parameters that do not exist here.

---

## 8a. What the first flight taught (2026-08-06)

### The control-link failsafe fired for real

```
14:45:58  vehicle armed
14:46:45  FAILSAFE: control link lost (616 ms) -> commanding LAND
14:46:46  failsafe latch cleared: vehicle disarmed
```

47 seconds in, at altitude, the ground-to-drone control path went silent past the
600 ms threshold. The daemon neutralised the sticks, released the RC override and
commanded LAND. It latches by design, so the GUI stayed dead until the aircraft
disarmed. **Nothing malfunctioned.** This is the failsafe working, and it is the
only time it has fired outside a test.

This was **not** the RC transmitter. That message comes from our daemon watching
Wi-Fi packet age; an RC dropout produces ArduPilot's own `Radio Failsafe`.

### Why: the link is asymmetric, and control rides the weak half

Measured during the same session:

| Direction | Signal | Carries |
|---|---|---|
| Ground hears drone | -74 dBm | video, telemetry |
| **Drone hears ground** | **-86 dBm** | **stick input** |

The drone runs a USB Archer T2U at 20 dBm. The ground runs the Jetson's onboard
`rtl88x2ce`, **hardware-capped at 15 dBm** — verified, the driver accepts a 20 dBm
request, reports success, and ignores it. Setting the regulatory domain does not
help; it was never the limit.

So video degrades visibly and looks like the problem, while control has less
margin and fails silently until the failsafe catches it.

### And a large part of it was a wall

Signal across the session: **-30 dBm** on the bench, **-56 dBm** with both
machines outside, **-71 dBm** immediately after the ground station was carried
back indoors to reach a wired ethernet port. That 15 dB step is a building wall,
and it is larger than any hardware change would buy.

**Fly with the ground station outside and untethered.** Note the irony worth
avoiding: it was indoors because a remote operator asked for ethernet to keep
visibility. Read the logs afterwards instead.

### Pitch was reversed, and it was found in the air

GUI pitch-forward commanded nose-up. Fixed with `DRONE_INVERT_PITCH=1` in
`/etc/drone/drone.env`. **Roll, yaw and throttle have still never been checked.**
See gotcha 11.

---

## 9. Gotchas

These cost real time to find. Most are not visible in code.

**1. `SYSID_MYGCS` mismatch silently kills the sticks.** ArduPilot discards
`RC_CHANNELS_OVERRIDE` from any system ID other than `SYSID_MYGCS`. The vehicle
was set to 255 while the daemon transmitted as 250, so **no stick input had ever
reached the aircraft**. Arm, mode and takeoff use `COMMAND_LONG`, accepted from
anyone, which is why those worked and made it look like a GUI bug.

**2. The flight controller's parameters contradicted its own documentation.**
Three of six safety parameters were wrong: `ARMING_CHECK` was 0 (every pre-arm
check disabled), and both battery failsafe actions were 0 (detects a flat pack,
does nothing about it). Read parameters off the vehicle. Never trust a README,
including this one.

**3. Login authentication had a bypass, now fixed.** The bridge decided a
password was correct by probing and seeing whether telemetry arrived. But the
daemon streams telemetry to every address it has ever accepted a packet from, so
after any correct login, the long-lived session socket received telemetry
regardless of what token was sent. **Any password was accepted**, producing a
cockpit with live video and telemetry and zero control authority. Fixed by
probing from a fresh UDP socket. Regression test in `tests/test_auth_probe.py`,
verified to fail against the pre-fix code.

**4. `PreArm: Need Position Estimate` is a MODE requirement, not an arming
check.** GUIDED, LOITER, POSHOLD and RTL each demand a position estimate by
virtue of being that mode. Setting `ARMING_CHECK=0` does not help. **ALT_HOLD
needs only the barometer** and is the mode for bench work.

**5. The battery percentage is a coulomb counter, not a charge estimate.** It
resets to about 100% on every power-up regardless of actual charge. It read 98%
on a pack sitting at 14.6 V, which on 4S is roughly 30-40%. **Trust voltage.**

**6. PPM caps the transmitter at 8 channels.** The FS-i6X is a 10-channel radio
but the receiver feeds the FC over PPM, so channels 9 and 10 never arrive. The
pilot-takeover feature documented in the airborne README needs channels 9-12 to
mirror the sticks onto, so **it cannot work as designed on this setup**. Switch
the receiver to i-BUS, or rely on the ch5 mode switch, which is never overridden
and is currently the only takeover path.

**7. The detector loses a memory race at boot.** The ground Jetson runs a full
GNOME desktop; with Firefox open it drops under 100 MB free on an 8 GB board, and
TensorRT aborts with `NvMapMemAllocInternalTagged ... error 12` (ENOMEM, and
NvMap cannot swap). Worse, it aborts **after** the HTTP thread has bound :8091,
so the port stays open with nothing behind it and systemd reports the service as
`active` while it is dead. `run-container.sh` now waits for headroom first. Close
the browser and the software updater before flying.

**8. GUI changes need a rebuild and a hard reload.** The bridge serves
`gui/dist`, not `gui/src`. After `npm run build`, a plain F5 reuses the cached
`index.html` which still points at the old bundle, so a correct build appears to
do nothing. Use Ctrl+Shift+R.

**9. `pymavlink` raises `TypeError` from inside `recv_match`** on certain message
instances. It aborted a parameter write midway. Both MAVLink tools now catch it.
If you write new MAVLink code here, guard your reads.

**10. `pkill -f <pattern>` over SSH kills your own shell**, because the pattern
appears in the shell's own command line. Use the `[p]attern` bracket trick.

**11. Stick direction is a daemon-side convention, and it was wrong.** The GUI
sends pitch-forward as `+1`; the daemon mapped that to a high PWM on ch2, which
ArduPilot reads as nose-up. Corrected with `DRONE_INVERT_PITCH=1`. The transmitter
is unaffected because it feeds the flight controller directly. **If you ever set
an invert flag, verify the transmitter agrees** — if the GUI and the safety
pilot's sticks disagree on which way is forward, taking control back becomes the
dangerous moment rather than the safe one.

---

## 10. Immediate next steps

In order:

1. **Charge the battery** and confirm the cell count is 4S
2. **50 m walk test** with `tools/linkcheck.sh` on the ground Jetson. Outdoors,
   line of sight, sensor at flight height. Watch TX rate, which degrades before
   signal does.
3. **Change the session token** from its bench value in `/etc/drone/drone.env`
4. **Stage 2** outside with props off: arm, confirm the armed state comes from
   the vehicle, then stop `ground-bridge` while armed and confirm the daemon
   commands LAND within about 0.6 s
5. **Stage 3** outside with props off: override direction check on all four axes.
   This has never been validly performed.
6. **Stage 4**: first hover, props on, open area, someone on the transmitter

The staged bring-up is documented in the airborne README. It exists for a reason
and should not be compressed.

---

## 11. Non-negotiable constraints

**Radio.** 2.4 GHz TX power is capped at 20 dBm to stay inside FCC limits. The
Realtek driver will accept and report 30 dBm with no regard to the regulatory
database. Do not raise it. This is university equipment.

**Bench relaxations must be restored.** Arming indoors requires
`ARMING_CHECK=0` and `FENCE_ENABLE=0`. Both must go back to 1 before the aircraft
goes outside, and props stay off whenever they are relaxed.

**The airborne daemon is the safety boundary**, not the ground station. It
re-validates every packet, refuses overrides outside allowed modes, confirms arm
state from the vehicle rather than from a button press, and runs an active
link-loss failsafe. Nothing on the ground side can override that, and nothing
should try.

---

## 12. Open questions worth researching

Honest list, with the reasoning rather than just the topic.

**Receiver protocol.** Moving from PPM to i-BUS would recover channels 9 and 10
and make partial pilot-takeover possible. Is two-axis takeover detection worth
having, or is the ch5 mode switch sufficient? The latency difference is roughly
50-120 ms versus up to 1 s.

**Ground station memory.** The detector is one Firefox window away from failing
at boot. Options include a lighter desktop session, a kiosk-mode browser, or
moving inference off the desktop entirely. What is the least invasive fix that
survives a student opening a browser?

**A second, independent radio link (mLRS).** The strongest open direction. mLRS
(github.com/olliw42/mLRS, supported in ArduPilot docs) carries RC control *and*
MAVLink telemetry over one LoRa link, 868/915 MHz or 2.4 GHz, at ranges far past
what the Wi-Fi manages. **Buy the 900 MHz variant** — the 2.4 GHz one would share
a band with the video hotspot on channel 11, which is the problem, not the fix.
Note a TX module and an RX module are both required; some listings sell only one.

Three possible topologies, in increasing order of ambition:

1. **RX to the Pixhawk, MAVLink only.** What mLRS is designed for. Gives an
   independent telemetry and backup-command path that survives total Wi-Fi loss,
   viewable in Mission Planner. Works out of the box. **Start here.**
2. **RX to the airborne Jetson.** Physically fine (40-pin UART or a USB-serial
   adapter), and architecturally *better* for this project's safety model: it
   keeps the daemon as the Pixhawk's only commander and hands it a redundant
   ground link, rather than introducing a second MAVLink source to the flight
   controller. The catch is bandwidth. A `ctrl` packet is ~110 bytes at 50 Hz,
   roughly **44 kbps**, which is a lot for a long-range LoRa mode. This needs a
   compact binary encoding, a lower rate, or a faster/shorter-range mLRS mode
   before it is viable. mLRS does support a transparent serial mode alongside
   MAVLink, so the pipe exists; the budget is the question.
3. **Move GUI control onto mLRS entirely.** Takes Wi-Fi out of the control path,
   leaving it to carry only video. A genuine redesign — the failsafe state
   machine would have to be re-sited — and it should be scoped as its own piece
   of work with the 2026-08-06 failsafe log as the motivating evidence.

**Whichever topology: mind the system ID.** `SYSID_MYGCS=250` exists so the
daemon's RC overrides are accepted while a second GCS on 255 can monitor without
its joystick injecting anything (see gotcha 1). Adding a second MAVLink source
without deciding its sysid reproduces exactly the "sticks silently do nothing"
failure that cost a day to find.

mLRS cannot carry video. Ten Mbit/s of MJPEG will never fit down a LoRa link, so
Wi-Fi keeps that job regardless. The end state is three separate paths — mLRS for
safety-critical low-bandwidth traffic, Wi-Fi for bulk data, RC for manual
takeover — against today's two, one of which carries almost everything.

**Ground station radio is the bottleneck, and software cannot fix it.** The link
is asymmetric by 12 dB: measured 2026-08-06, the ground heard the drone at
-74 dBm while the drone heard the ground at -86 dBm. The weak direction is
ground-to-drone, which carries stick input, and it produced a real in-flight
control-link failsafe at altitude (616 ms gap, daemon commanded LAND). The drone
runs a USB Archer T2U at 20 dBm; the ground runs the Jetson's onboard rtl88x2ce,
which is hardware-capped at 15 dBm regardless of regulatory domain — verified,
the driver accepts a 20 dBm request and ignores it. **A second Archer T2U for the
ground station is the single highest-value hardware change available to this
project.**

**Link margin at range.** Bench numbers give roughly 4x headroom on video. Nobody
knows what happens at 50 m with the aircraft airborne and the antennas oriented
as they will actually be. Predicted around -66 dBm, entirely untested.

**Detection value.** The system detects COCO classes. Nobody has stated what the
aircraft is actually meant to detect. If it is a specific target class, a
retrained or different model may matter far more than the current SSD-vs-SSDLite
discussion ever did.

**Stale ground tests.** The three failing tests need a fixture that completes the
auth handshake, which means the fake airborne side must reply with telemetry to a
token-bearing probe. Not hard, but it touches the auth path that was recently
fixed, so it deserves care.
