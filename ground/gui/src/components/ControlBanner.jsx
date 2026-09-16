import { CTRL_DEAD_MS } from "../config.js";

/**
 * Raised when the aircraft is not acting on our control packets.
 *
 * `ctrl_age_ms` is the aircraft's own report of how long since it last *accepted*
 * a control packet from us. It climbing while we are connected means our packets
 * are arriving and being discarded — a wrong session token, a protocol mismatch,
 * or a one-way radio path.
 *
 * This is worth a banner rather than a coloured chip because every other
 * indicator keeps looking healthy: telemetry, video and detections all flow over
 * paths that do not depend on the token being right, so the cockpit reads
 * perfectly normal while nothing the operator does reaches the vehicle.
 */
export default function ControlBanner({ tlm }) {
  const age = tlm?.ctrl_age_ms;
  if (age == null || age < CTRL_DEAD_MS) return null;

  return (
    <div id="cbanner" className="on">
      <span>⚠ NO CONTROL AUTHORITY — aircraft is discarding our commands</span>
      <span className="cbanner-age">last accepted {(age / 1000).toFixed(1)} s ago</span>
    </div>
  );
}
