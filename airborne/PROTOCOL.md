# Wire protocol

All messages are UTF-8 JSON. One JSON object per UDP datagram / per WebSocket frame.

```
browser  --WS(JSON)-->  ground bridge  --UDP(JSON)-->  airborne daemon  --MAVLink--> Pixhawk
browser  <-WS(JSON)--  ground bridge  <-UDP(JSON)--  airborne daemon  <-MAVLink-- Pixhawk
```

The browser never stores a token in config: the operator types it at login. The
ground bridge injects that token into every uplink packet before forwarding.

## Login (browser ↔ bridge only, never forwarded)

The bridge holds no drone connection until a browser authenticates. The GUI
opens on the fleet screen; pressing Fly on an aircraft asks for its password,
and the password **is** that aircraft's token.

```json
browser → bridge:  { "t":"auth", "password":"...", "drone":"Omega" }
bridge  → browser: { "t":"auth_ok", "airborne":"10.130.143.5", "drone":"Omega" }
bridge  → browser: { "t":"auth_fail", "reason":"Omega did not accept that password" }
```

`drone` is optional. With it, the bridge probes only that aircraft from
`/etc/drone/fleet.json`, so a login aimed at one aircraft can never land on
another that happens to share a password; an unknown name or an aircraft marked
`"ready": false` is refused without probing anything. Without it, the bridge
probes each candidate IP (`GROUND_AIRBORNE_IPS`, comma-separated) in order, and
`auth_ok` names the aircraft if its address is in the fleet file. Either way the
probe is a token-bearing heartbeat followed by a wait for telemetry. Because the airborne daemon only records a
return address — and thus only sends telemetry — for packets whose token it
accepts, a reply proves the password is correct **and** identifies which drone it
belongs to.

> ⚠ **The probe must be sent from a freshly bound UDP port.** The daemon streams
> telemetry to the most recent address that sent it a valid packet. Probing from
> a socket the daemon is already streaming to means
> a reply arrives no matter what token was sent, so the check silently degrades
> into "is this drone talking to us" and **any password is accepted**. That was a
> real bug: the operator got a cockpit with live telemetry and video but zero
> control authority, because the aircraft then rejected every command packet as a
> bad token. See `test_auth_probe.py` in the ground repo.

Wrong password → no drone replies → `auth_fail`. The password/token is
never sent to the drone as anything other than the standard per-packet `token`,
and the `/camera` proxy is gated the same way (no login → 502). Telemetry and
control flow only after `auth_ok`; the session is dropped when the last client
disconnects, which is also how the cockpit's Fleet button ends a session. The GUI
silently re-authenticates, to the same aircraft, across bridge reconnects.

## Fleet API (browser ↔ bridge only, HTTP)

```
GET  /api/fleet                 every aircraft: name, ip, ready, reachable,
                                active, can_check_health, health, health_age_s
POST /api/fleet/{name}/health   refresh one aircraft's health reading
```

Tokens never appear in either response. Reachability is a TCP connect to the
drone Jetson's port 22, so polling it never touches UDP 14650. A health check
sends one token-bearing heartbeat from a fresh port and keeps a whitelisted
summary of the reply. The aircraft being flown is **never** probed, because the
daemon streams telemetry to the most recent valid sender and a probe would take
the cockpit's stream; its health comes from the live session instead. Responses:
`404` unknown name, `409` in build or no token in the fleet file.

## Uplink: control (browser/bridge → airborne), ~50 Hz
```json
{ "t":"ctrl", "seq":41821, "ts":1783448400123,
  "roll":0.00, "pitch":-0.23, "thr":0.00, "yaw":0.00, "token":"..." }
```
- `seq`   monotonically increasing integer. Airborne rejects `seq <= last_seq`.
- `ts`    ms since epoch (browser clock), informational.
- axes    floats in **[-1.0, +1.0]**. `thr` is **self-centering**: `0` = hold altitude.
- Any axis out of range → the whole packet is rejected.

## Uplink: command (browser/bridge → airborne)
```json
{ "t":"cmd", "cmd":"arm",     "token":"..." }
{ "t":"cmd", "cmd":"disarm",  "token":"..." }
{ "t":"cmd", "cmd":"set_mode","mode":"LOITER", "token":"..." }
{ "t":"cmd", "cmd":"takeoff", "alt":5.0, "token":"..." }   // arm + GUIDED climb -> LOITER
{ "t":"cmd", "cmd":"failsafe","token":"..." }        // manual failsafe -> LAND
{ "t":"cmd", "cmd":"resume",  "token":"..." }        // clear a latched failsafe
```
`arm` runs airborne-side preconditions (fresh control, FC connected, allowed mode,
throttle centered) then sends MAV_CMD_COMPONENT_ARM_DISARM and confirms the result
from the vehicle's HEARTBEAT before reporting `armed`.

`takeoff` carries `alt` (metres, `(0, 120]` at the wire layer; the airborne daemon
clamps to `DRONE_TAKEOFF_MAX_ALT_M`, default 30). It runs the same preconditions,
switches to **GUIDED**, arms and confirms from HEARTBEAT, then sends
`MAV_CMD_NAV_TAKEOFF`. The control loop watches altitude and, once ~90% of target
is reached, hands piloting back in the default pilot mode (**LOITER**). Disarming,
selecting any mode during the climb, or a failsafe cancels the handoff. Stick
overrides stay disabled during the GUIDED climb. Disabled by `DRONE_ALLOW_TAKEOFF=0`;
`alt` is rejected on any other command.

## Uplink: heartbeat (bridge → airborne), 1 Hz
```json
{ "t":"hb", "token":"..." }
```
Lets the airborne daemon learn the bridge's address so telemetry flows even when
no one is flying.

## Downlink: telemetry (airborne → bridge → browser), ~10 Hz
```json
{ "t":"tlm", "ts":..., "connected":true, "armed":false, "mode":"LOITER",
  "mode_num":5, "hb_age_ms":42, "gps_fix":3, "sats":18, "batt_v":15.8,
  "batt_pct":87, "current_a":2.1, "alt":1.20, "gspeed":0.30, "ekf_ok":true,
  "ctrl_age_ms":18, "failsafe":false, "allowed_modes":["ALT_HOLD","LOITER","POSHOLD"],
  "statustext":"...", "events":[{"kind":"armed","msg":"vehicle ARMED","ts":...}] }
```

## Video (out of band)

The camera feed is **not** part of the JSON wire protocol — it is a separate HTTP
MJPEG stream so it can never stall the control/telemetry path.

```
webcam --USB--> airborne camera_daemon --MJPEG/HTTP--> ground bridge /camera --> browser <img>
```

- `camera_daemon` (drone Jetson) auto-detects a USB/UVC webcam, keeps it open with
  auto-reconnect, and serves `multipart/x-mixed-replace` at `:8090/stream.mjpg`
  (`/snapshot.jpg`, `/healthz` too). Runs as its own process; not flight-critical.
- The ground bridge reverse-proxies it at `/camera/stream.mjpg` so the browser
  stays same-origin (no drone IP in the GUI). Returns `502` when the camera is
  offline; the GUI then shows the placeholder and retries.

## Ports (defaults)
| Port | Proto | Where | Purpose |
|------|-------|-------|---------|
| 14650 | UDP | airborne Jetson | control/command/hb ingest |
| 8090  | TCP | airborne Jetson | camera MJPEG stream (camera_daemon) |
| 8000  | TCP | ground Jetson | GUI, WebSocket `/ws`, fleet API, `/camera` proxy |
