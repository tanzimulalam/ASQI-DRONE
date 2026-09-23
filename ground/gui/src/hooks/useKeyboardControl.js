import { useEffect, useRef } from "react";
import { KEY_MAX_DEFLECTION, KEY_RISE_MS, KEY_FALL_MS } from "../config.js";

/**
 * Keyboard piloting, helicopter-sim style.
 *
 *   W / S            throttle up / down   (self-centering: release = hold altitude)
 *   A / D            yaw left / right
 *   Numpad 8 / 2     pitch forward / back
 *   Numpad 4 / 6     roll left / right
 *
 * Arrow keys mirror the numpad so a laptop without a number pad can still fly.
 *
 * Keys are matched on `event.code` (physical key), never `event.key`: the numpad
 * reports "Numpad8" regardless of NumLock or keyboard layout, whereas `key` would
 * be "8" or "ArrowUp" depending on both.
 *
 * A held key is a step input, which is far more abrupt than a thumb on a gimbal.
 * So the axis is *ramped* toward its target rather than snapped, and capped at
 * `KEY_MAX_DEFLECTION` of full stick. Release ramps back down faster than press
 * ramps up (`KEY_FALL_MS` < `KEY_RISE_MS`) because neutralizing should never be
 * the slow direction.
 *
 * Safety: a keyup that never arrives would pin an axis at full deflection. Losing
 * window focus (alt-tab, notification, the browser opening a dialog) does exactly
 * that. So blur, page-hide and disable all clear every held key and snap the axes
 * to zero immediately rather than ramping.
 *
 * Follows the same contract as Stick: axis values are written into `axesRef` and
 * never held in React state, so flying does not re-render the cockpit.
 *
 * @param axesRef   shared mutable axes object from useDroneLink
 * @param enabled   whether keyboard control is active
 * @param onKeysChange  optional callback with the Set of held key codes; fires
 *                      only on actual press/release, not per animation frame
 */

// physical key code -> [axis, direction]
const BINDINGS = {
  KeyW: ["throttle", +1],
  KeyS: ["throttle", -1],
  KeyA: ["yaw", -1],
  KeyD: ["yaw", +1],
  Numpad8: ["pitch", +1],
  Numpad2: ["pitch", -1],
  Numpad4: ["roll", -1],
  Numpad6: ["roll", +1],
  ArrowUp: ["pitch", +1],
  ArrowDown: ["pitch", -1],
  ArrowLeft: ["roll", -1],
  ArrowRight: ["roll", +1],
};

const AXES = ["roll", "pitch", "throttle", "yaw"];
const EMPTY_AXES = new Set();

/** True when the event came from somewhere the user is typing. */
function isTypingTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

export function useKeyboardControl(axesRef, enabled, onKeysChange, ownedAxes) {
  // Keep the callback in a ref so re-renders don't tear down the key listeners.
  const cbRef = useRef(onKeysChange);
  cbRef.current = onKeysChange;

  // Axes somebody else is driving (the altitude hold owns the throttle while it
  // runs). Writing them here as well would mean two writers per frame.
  const owned = ownedAxes || EMPTY_AXES;

  useEffect(() => {
    const held = new Set();
    // Ramped current value per axis, independent of whatever the sticks last wrote.
    const cur = { roll: 0, pitch: 0, throttle: 0, yaw: 0 };

    const notify = () => cbRef.current?.(new Set(held));

    /** Zero everything, right now, with no ramp. */
    const panic = () => {
      const hadKeys = held.size > 0;
      held.clear();
      for (const axis of AXES) {
        cur[axis] = 0;
        if (!owned.has(axis)) axesRef.current[axis] = 0;
      }
      if (hadKeys) notify();
    };

    if (!enabled) {
      panic();
      return undefined;
    }

    const onKeyDown = (e) => {
      if (!BINDINGS[e.code] || isTypingTarget(e.target)) return;
      // Stop arrows scrolling the page and space-like keys activating buttons.
      e.preventDefault();
      if (e.repeat || held.has(e.code)) return; // auto-repeat is not a new press
      held.add(e.code);
      notify();
    };

    const onKeyUp = (e) => {
      if (!BINDINGS[e.code]) return;
      e.preventDefault();
      if (held.delete(e.code)) notify();
    };

    const onVisibility = () => {
      if (document.hidden) panic();
    };

    let raf = 0;
    let last = performance.now();

    const tick = (now) => {
      const dt = Math.min(now - last, 100); // clamp so a stalled tab can't jump
      last = now;

      // Target per axis: opposing keys cancel, then scale to the deflection cap.
      const target = { roll: 0, pitch: 0, throttle: 0, yaw: 0 };
      for (const code of held) {
        const [axis, dir] = BINDINGS[code];
        target[axis] += dir;
      }

      for (const axis of AXES) {
        if (owned.has(axis)) {
          cur[axis] = 0;          // stay neutral so handing the axis back is smooth
          continue;
        }
        const want = Math.max(-1, Math.min(1, target[axis])) * KEY_MAX_DEFLECTION;
        const returning = Math.abs(want) < Math.abs(cur[axis]) || want * cur[axis] < 0;
        const rampMs = returning ? KEY_FALL_MS : KEY_RISE_MS;
        // rampMs is the time to cross the full deflection range.
        const step = (dt / rampMs) * KEY_MAX_DEFLECTION;
        const delta = want - cur[axis];
        cur[axis] = Math.abs(delta) <= step ? want : cur[axis] + Math.sign(delta) * step;
        axesRef.current[axis] = cur[axis];
      }

      raf = requestAnimationFrame(tick);
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", panic);
    document.addEventListener("visibilitychange", onVisibility);
    raf = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", panic);
      document.removeEventListener("visibilitychange", onVisibility);
      cancelAnimationFrame(raf);
      panic();
    };
  }, [axesRef, enabled, owned]);
}

export { BINDINGS };
