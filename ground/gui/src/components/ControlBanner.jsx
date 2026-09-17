import { CTRL_DEAD_MS } from "../config.js";

/**
 * Raised when the operator's control input is not reaching the aircraft.
 *
 * Two different failures look identical from the cockpit and need different
 * wording, because the fix for each is different.
 *
 * `txOk === false` means no control packet has left this browser recently. The
 * aircraft is not discarding anything, nothing is being sent. Historically this
 * happened when the tab was backgrounded and the browser throttled the sender to
 * roughly 1 Hz. The sender now runs off a worker tick so that specific cause is
 * fixed, but a frozen page stops workers too, so the cockpit still measures
 * whether packets actually go out instead of assuming they do.
 *
 * `ctrl_age_ms` climbing while we are transmitting is the opposite case: our
 * packets are arriving and being rejected. A wrong session token, a protocol
 * mismatch, or a one-way radio path.
 *
 * Either is worth a banner rather than a coloured chip, because every other
 * indicator keeps looking healthy: telemetry, video and detections all flow over
 * paths that do not depend on the control path working, so the cockpit reads
 * perfectly normal while nothing the operator does reaches the vehicle.
 */
export default function ControlBanner({ tlm, txOk = true }) {
  if (!txOk) {
    return (
      <div id="cbanner" className="on">
        <span>⚠ COCKPIT NOT TRANSMITTING</span>
        <span className="cbanner-age">no control packets are leaving this browser</span>
      </div>
    );
  }

  const age = tlm?.ctrl_age_ms;
  if (age == null || age < CTRL_DEAD_MS) return null;

  return (
    <div id="cbanner" className="on">
      <span>⚠ NO CONTROL AUTHORITY: aircraft is discarding our commands</span>
      <span className="cbanner-age">last accepted {(age / 1000).toFixed(1)} s ago</span>
    </div>
  );
}
