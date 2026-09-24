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

// The cockpit is considered to have stopped transmitting if no control packet has
// actually left the browser within this window (ms). Deliberately below the
// airborne failsafe threshold (600 ms) so the operator is told the uplink has
// died slightly before the aircraft acts on it, and well above the 20 ms send
// period so ordinary jitter never raises it.
export const TX_STALL_MS = 300;

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

// ======= keyboard piloting =======
// A held key is a step input, unlike a thumb easing a gimbal over. These tame it.
//
// Fraction of full stick a held key can reach. Deliberately below 1.0: the goal
// is a flyable aircraft, not maximum authority. Raise it once the airframe is
// trimmed and the operator is comfortable on the sticks.
export const KEY_MAX_DEFLECTION = Number(import.meta.env.VITE_KEY_MAX ?? 0.6);
// Time (ms) to ramp across the full deflection range when pressing a key...
export const KEY_RISE_MS = 300;
// ...and when releasing one. Shorter on purpose: returning to neutral should
// never be the slow direction.
export const KEY_FALL_MS = 150;

// Control is considered not reaching the aircraft once its reported ctrl age
// passes this. Well above the airborne failsafe window (600 ms) so a brief
// network hiccup does not raise the banner, low enough that a genuinely dead
// command path is obvious within a second.
export const CTRL_DEAD_MS = 1000;

// ======= height presets =======
// Number keys 1..6 fly to a height and hold it; backtick commands the flight
// controller's own LAND. Heights are metres above where the aircraft armed,
// which is what the barometer reports. 2.1 m is about seven feet.
export const HOLD_HEIGHTS_M = [0.5, 0.8, 1.1, 1.4, 1.7, 2.1];
// Stick offset per metre of error. 0.45 gives roughly 1 m/s of climb at a metre
// out, which settles without overshooting into a ceiling.
export const HOLD_KP = 0.45;
// Asymmetric on purpose: more authority up than down.
export const HOLD_MAX_UP = 0.35;
export const HOLD_MAX_DOWN = -0.25;
// Close enough. Below this the aircraft is left alone rather than hunting.
export const HOLD_TOL_M = 0.08;
// The reported height wanders indoors (0.4 m in 25 s, measured on the bench with
// the aircraft stationary), so the hold flies a smoothed version of it. At 10 Hz
// telemetry this is roughly a half-second time constant: enough to ignore the
// wander, quick enough to catch a real climb.
export const ALT_SMOOTHING = 0.25;
// A jump larger than this is a change of reference, not movement: arming re-sets
// home, and the height jumps by however far off the old home was. Snap, do not ramp.
export const ALT_STEP_RESET_M = 1.0;
