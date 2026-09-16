# Transmitter GUI (React + Vite)

FPV touchscreen transmitter for the ground Jetson's 7" panel. Two self-centering
sticks (Mode 2), a video slot with on-screen object detection, full telemetry,
and ARM & TAKE OFF / RTL / DISARM.

## Login

On load the GUI shows a password box and nothing else — no sticks, telemetry, or
video until the operator authenticates. The password **is** the drone token
(`DRONE_SESSION_TOKEN`). The bridge probes its candidate IPs
(`GROUND_AIRBORNE_IPS`) and connects to the drone that accepts it; a wrong
password connects to nothing. See `PROTOCOL.md` → "Login". The connected drone's
IP shows as a chip in the telemetry bar.

## Layout

```
gui/
  index.html            Vite entry template (dev only)
  src/
    main.jsx            React entry
    App.jsx             page shell — wires the pieces together
    config.js           video URL, altitude bounds, throttle tol, rates
    hooks/useDroneLink.js      WebSocket + 50 Hz control uplink + telemetry + boxes
    hooks/useObjectDetection.js  UNUSED — old in-browser TF.js loop (see below)
    components/         Stick, TelemetryBar, VideoPanel, CommandDeck,
                        TakeoffModal, FailsafeBanner
    styles.css          7"-tuned styling (identical look to the legacy GUI)
  public/models/coco-ssd/   UNUSED — old vendored TF.js model, 18 MB (see below)
  dist/                 build output — served by the ground bridge (gitignored)
  legacy/index.html     the original single-file vanilla GUI (bridge fallback)
```

## Develop

Run the ground bridge (`python -m app <AIRBORNE_IP>`) on :8000, then:

```bash
npm install        # first time
npm run dev        # Vite dev server on http://localhost:5173 with HMR
```

The dev server proxies `/ws`, `/healthz`, `/readyz` to the bridge on :8000
(override with `BRIDGE_ORIGIN`), so it talks to real hardware while you edit.

## Build for the touchscreen

```bash
npm run build      # -> gui/dist/   (or: scripts/build_gui.sh)
```

The ground bridge auto-detects `gui/dist/` and serves it as a static SPA at `/`.
If `dist/` is absent it falls back to `legacy/index.html`, so the system still
runs before the first build.

## Performance contract

Stick motion must never re-render React. Axis values live in a mutable ref
(`axesRef` in `useDroneLink`); `Stick` writes into it and moves the knob via a DOM
ref, and the 50 Hz sender reads it directly. Only telemetry (~10 Hz), detection
boxes, and coarse link state flow through `useState`. Keep new per-frame values
(stick position, control packets) out of component state — use the ref.

The detection canvas follows the same rule in spirit: it only reallocates its
backing store when the element actually changes size, not on every box update.

## Configure

- **Video**: defaults to `/camera/stream.mjpg` — the ground bridge's same-origin
  reverse proxy of the drone's `camera_daemon` (auto-detecting webcam streamer on
  the airborne Jetson, port 8090). Nothing to configure for the normal setup. To
  point the browser straight at the camera and bypass the proxy, set
  `VITE_VIDEO_URL=http://<drone-ip>:8090/stream.mjpg` at build time. See
  `PROTOCOL.md` → "Video".
- **Altitude bounds / throttle tolerance**: `config.js` (keep in sync with the
  airborne `DRONE_TAKEOFF_MAX_ALT_M` and `arm_throttle_center_tol`).
- **Object detection**: the overlay is on by default; toggle with the
  `DETECT ON/OFF` chip on the video, or set `VITE_DETECT_ON=0` to default it off.
  Everything else (model, confidence, box cap) is configured on the detector —
  see below.

## Object detection

Boxes are computed on the **ground Jetson's GPU** (TensorRT, via
[jetson-inference](https://github.com/dusty-nv/jetson-inference)) and arrive over
the telemetry WebSocket as `det` messages. The browser only draws them onto a
canvas over the feed — it loads no model and touches no WebGL.

- **Why not in the browser.** This used to run COCO-SSD in TensorFlow.js.
  Chromium on the Jetson has no hardware WebGL, so the 18 MB model took ~54 s to
  become ready and then managed ~1 inference/sec — indistinguishable from a hang.
  Moving inference to TensorRT removed the model download, the WebGL dependency,
  and the main-thread stalls at once, and cut the JS bundle from ~1.9 MB to
  ~154 kB.
- **Where it runs.** `mtsuissl-ground/detector/`, inside the dusty-nv container
  (`sudo ./detector/run-container.sh`). It pulls the drone's MJPEG feed directly
  and always detects on the *newest* frame — inference is slower than 30 fps, so
  stale frames are dropped rather than queued, and boxes describe the scene now
  instead of several seconds ago. Results are served on `:8091`; the bridge
  long-polls and fans them out.
- **When it's absent.** Nothing breaks. The bridge retries quietly, the badge
  reads `detector offline`, and video is unaffected. If detections stop arriving
  for `DET_STALE_MS` (2 s) the overlay clears — stale boxes over live video claim
  things about the scene that are no longer true.

Tuning lives with the detector rather than here, since that's the side doing the
work: `DET_NETWORK` (default `ssd-mobilenet-v2`), `DET_THRESHOLD`, `DET_MAX_BOXES`.
Swap models by pointing `DET_NETWORK` at another detectNet name or a custom ONNX
path.

> The old in-browser path is still on disk but unused: `hooks/useObjectDetection.js`,
> `public/models/coco-ssd/` (18 MB), and the `@tensorflow/*` entries in
> `package.json`. Safe to delete once you're happy with the TensorRT path.
