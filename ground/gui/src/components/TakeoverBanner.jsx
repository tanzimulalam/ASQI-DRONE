/**
 * Shown while the airborne daemon has latched an RC-pilot takeover: the safety
 * pilot touched the transmitter sticks, so the daemon released its override and
 * the GUI sticks are inert. RESUME hands control back — only press it once the
 * safety pilot's sticks are neutral and they've agreed to give the aircraft back.
 */
export default function TakeoverBanner({ active, onResume }) {
  if (!active) return null;
  return (
    <div id="tobanner" className="on">
      ✋ RC PILOT HAS CONTROL — GUI sticks disabled
      <button id="resume" onClick={onResume}>
        RESUME WEB CONTROL
      </button>
    </div>
  );
}
