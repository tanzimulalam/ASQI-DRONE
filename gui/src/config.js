// ======= runtime configuration =======

// FPV video source. Defaults to the ground bridge's same-origin proxy of the
// drone camera_daemon (/camera/stream.mjpg), so no drone IP is baked into the
// browser. Override with VITE_VIDEO_URL at build time to point straight at the
// camera (e.g. "http://10.42.0.1:8090/stream.mjpg") and bypass the proxy.
export const VIDEO_URL = import.meta.env.VITE_VIDEO_URL ?? "/camera/stream.mjpg";

// Takeoff altitude prompt bounds (must stay <= the airborne DRONE_TAKEOFF_MAX_ALT_M).
export const ALT_MIN = 1;
export const ALT_MAX = 30;
export const ALT_STEP = 0.5;
export const ALT_DEFAULT = 5;

// Throttle must be within this of center to arm. Match airborne arm_throttle_center_tol.
export const THR_CENTER_TOL = 0.1;

// Control uplink rate (Hz) and its period in ms.
export const CTRL_HZ = 50;
export const CTRL_PERIOD_MS = Math.round(1000 / CTRL_HZ);

// Telemetry considered dead if none arrives within this window (ms).
export const TLM_STALE_MS = 1500;

// ======= on-screen object detection =======
// Inference runs on the GROUND JETSON's GPU (TensorRT via jetson-inference), not
// in the browser: the `detector` service consumes the drone's MJPEG feed and the
// bridge forwards boxes over the telemetry WebSocket as `det` messages. That keeps
// the browser free of an 18 MB model download and of any WebGL dependency, which
// matters because Chromium on the Jetson has no hardware WebGL at all.
//
// Confidence cutoff and box cap now live with the detector (DET_THRESHOLD,
// DET_MAX_BOXES) since it is the side that does the work.

// Whether the overlay is on when the cockpit first loads (toggle in the UI).
export const DETECT_ON_DEFAULT = (import.meta.env.VITE_DETECT_ON ?? "1") !== "0";
// Hide the overlay if no `det` message arrives within this window (ms). Stale
// boxes over live video are worse than no boxes.
export const DET_STALE_MS = 2000;
