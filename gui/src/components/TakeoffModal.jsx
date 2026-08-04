import { useEffect, useRef, useState } from "react";
import { ALT_MIN, ALT_MAX, ALT_STEP, ALT_DEFAULT, THR_CENTER_TOL } from "../config.js";

const clampAlt = (v) => {
  if (!Number.isFinite(v)) v = ALT_DEFAULT;
  v = Math.max(ALT_MIN, Math.min(ALT_MAX, v));
  return +v.toFixed(1);
};

/**
 * Altitude prompt for ARM & TAKE OFF. Confirm is gated on a live throttle-centered
 * check polled from axesRef, mirroring the airborne-side arming precondition, so a
 * raised throttle stick can't launch. Confirm sends {cmd:"takeoff", alt}.
 */
export default function TakeoffModal({ open, axesRef, onConfirm, onCancel }) {
  const [alt, setAlt] = useState(String(ALT_DEFAULT));
  const [thrCentered, setThrCentered] = useState(true);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setAlt(String(ALT_DEFAULT));
    const check = () => setThrCentered(Math.abs(axesRef.current.throttle) <= THR_CENTER_TOL);
    check();
    const timer = setInterval(check, 150);
    return () => clearInterval(timer);
  }, [open, axesRef]);

  if (!open) return null;

  const commitField = () => setAlt(String(clampAlt(parseFloat(alt))));
  const step = (d) => setAlt(String(clampAlt(clampAlt(parseFloat(alt)) + d)));

  const confirm = () => {
    if (Math.abs(axesRef.current.throttle) > THR_CENTER_TOL) {
      setThrCentered(false);
      return;
    }
    onConfirm(clampAlt(parseFloat(alt)));
  };

  return (
    <div id="overlay" className="on" onClick={(e) => e.target.id === "overlay" && onCancel()}>
      <div id="modal">
        <h2>Arm &amp; Take Off</h2>
        <p>
          Arms and climbs straight up to the altitude below (via GUIDED), then hands
          control back in LOITER to hold position. Grab the sticks or press DISARM at
          any time to take over.
        </p>
        <div className="altfield">
          <input
            ref={inputRef}
            type="number"
            inputMode="decimal"
            min={ALT_MIN}
            max={ALT_MAX}
            step={ALT_STEP}
            value={alt}
            onChange={(e) => setAlt(e.target.value)}
            onBlur={commitField}
          />
          <span className="unit">m</span>
          <button className="stepbtn" onClick={() => step(-ALT_STEP)}>
            −
          </button>
          <button className="stepbtn" onClick={() => step(ALT_STEP)}>
            +
          </button>
        </div>
        {!thrCentered && (
          <div id="thrwarn" className="on">
            Release the throttle stick first — it must be centered to arm.
          </div>
        )}
        <div className="modalrow">
          <button id="mCancel" onClick={onCancel}>
            Cancel
          </button>
          <button id="mConfirm" onClick={confirm} disabled={!thrCentered}>
            ARM &amp; TAKE OFF
          </button>
        </div>
      </div>
    </div>
  );
}
