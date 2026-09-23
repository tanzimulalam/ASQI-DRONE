import { useEffect, useRef } from "react";
import { HOLD_KP, HOLD_MAX_UP, HOLD_MAX_DOWN, HOLD_TOL_M, TLM_STALE_MS } from "../config.js";

/**
 * Fly to, and hold, a chosen height.
 *
 * The operator picks a height with the number keys; this closes the loop by
 * writing the throttle axis, which in ALT_HOLD is a climb-rate command with
 * centre meaning "hold". The output is a small offset from centre, proportional
 * to the error, and it settles back to centre.
 *
 * Driven by telemetry arriving, not by a timer. A browser throttles timers and
 * animation frames in a window that is not in front, which is precisely how this
 * project once discovered a cockpit that had silently stopped flying the
 * aircraft (gotcha 12). WebSocket messages keep arriving regardless, so the
 * aircraft's own 10 Hz telemetry is both the trigger and the input. Between
 * updates the last command stands, which is what the 50 Hz sender transmits.
 *
 * Deliberately the least clever thing that works:
 *
 *   - proportional only. An integrator would fight the barometer's own drift and
 *     could wind up against a ceiling or somebody's hand.
 *   - clamped, and asymmetrically: less authority downward than upward, because
 *     a runaway descent ends on the floor.
 *   - a deadband, so the aircraft may sit still instead of hunting.
 *
 * It refuses to run unless the aircraft is armed, in ALT_HOLD, out of failsafe
 * and pilot takeover, with fresh telemetry. Any of those going away cancels the
 * hold rather than freezing the last command, and `onCancel` is told why so the
 * cockpit can say so.
 *
 * The caller must stop the keyboard writing the throttle while this runs, or the
 * two fight each other every frame. See `ownedAxes` in useKeyboardControl.
 */
export function useAltitudeHold(axesRef, { target, tlm, tlmAt, onCancel }) {
  const cbRef = useRef(onCancel);
  cbRef.current = onCancel;

  // Zero the axis whenever the hold stops, so a stale offset is never left
  // behind for the sender to keep transmitting.
  useEffect(() => {
    if (target == null) return undefined;
    return () => {
      axesRef.current.throttle = 0;
    };
  }, [target, axesRef]);

  useEffect(() => {
    if (target == null) return;

    const cancel = (why) => {
      axesRef.current.throttle = 0;
      cbRef.current?.(why);
    };

    if (!tlm || (tlmAt && Date.now() - tlmAt > TLM_STALE_MS)) return cancel("telemetry lost");
    if (!tlm.armed) return cancel("disarmed");
    if (tlm.failsafe) return cancel("failsafe");
    if (tlm.pilot_takeover) return cancel("pilot takeover");
    if (tlm.mode !== "ALT_HOLD") return cancel(`mode ${tlm.mode || "changed"}`);

    const alt = Number(tlm.alt);
    if (!Number.isFinite(alt)) return cancel("no altitude reading");

    const err = target - alt;
    axesRef.current.throttle =
      Math.abs(err) > HOLD_TOL_M
        ? Math.max(HOLD_MAX_DOWN, Math.min(HOLD_MAX_UP, HOLD_KP * err))
        : 0;
  }, [target, tlm, tlmAt, axesRef]);
}
