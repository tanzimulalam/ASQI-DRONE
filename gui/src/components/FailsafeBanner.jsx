export default function FailsafeBanner({ active, onResume }) {
  if (!active) return null;
  return (
    <div id="fsbanner" className="on">
      ⚠ FAILSAFE ACTIVE — LANDING
      <button id="resume" onClick={onResume}>
        RESUME
      </button>
    </div>
  );
}
