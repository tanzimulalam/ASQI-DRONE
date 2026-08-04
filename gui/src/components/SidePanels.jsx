/**
 * The flanking panels either side of the feed.
 *
 * A 4:3 camera on a 16:9 panel always leaves space to the left and right of the
 * video. Rather than leave it black it carries what an operator would otherwise
 * have to infer: what the detector sees and how hard it is working, and whether
 * each link in the chain is actually healthy.
 *
 * Everything here comes from real telemetry (`tlm`) or real detector output
 * (`det`). Nothing is synthesised — a field with no data reads "--" rather than
 * showing a plausible number.
 *
 * Both panels collapse on narrow screens (see styles.css) where the feed needs
 * the width more.
 */

// Same hue-from-label hash VideoPanel uses, so a class keeps one colour in both
// the box overlay and this list.
function classColor(label) {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
  return `hsl(${h}, 85%, 60%)`;
}

/** Freshness class for an age in ms, matching the telemetry bar's thresholds. */
function ageCls(ms, warn) {
  if (ms == null || ms < 0) return "x";
  return ms > warn * 3 ? "r" : ms > warn ? "y" : "g";
}

function StatRow({ label, value, cls, unit }) {
  return (
    <div className="statrow">
      <span className="statlabel">
        {cls && <span className={`dot ${cls}`} />}
        {label}
      </span>
      <span className="statvalue">
        {value}
        {unit && <span className="statunit">{unit}</span>}
      </span>
    </div>
  );
}

export function VisionPanel({ det }) {
  const boxes = det?.boxes ?? [];
  // Biggest score first; the detector already sorts, but don't rely on it.
  const rows = [...boxes].sort((a, b) => b.score - a.score).slice(0, 7);

  return (
    <aside className="sidepanel">
      <div className="panel-head">
        <span>Vision</span>
        {det && <span className="panel-count">{boxes.length}</span>}
      </div>

      <div className="panel-stats">
        <StatRow
          label="Rate"
          value={det ? Math.round(det.fps ?? 0) : "--"}
          unit=" fps"
          cls={det ? "g" : "x"}
        />
        <StatRow
          label="Inference"
          value={det?.inferMs != null ? det.inferMs.toFixed(1) : "--"}
          unit=" ms"
        />
        <StatRow
          label="Frame"
          value={det?.srcW ? `${det.srcW}×${det.srcH}` : "--"}
        />
      </div>

      <div className="panel-body">
        {!det && <div className="panel-empty">detector offline</div>}
        {det && rows.length === 0 && <div className="panel-empty">nothing in frame</div>}
        {rows.map((d, i) => (
          <div className="detrow" key={`${d.class}-${i}`}>
            <span className="detswatch" style={{ background: classColor(d.class) }} />
            <span className="detname">{d.class}</span>
            <span className="detscore">{Math.round(d.score * 100)}%</span>
            <span className="detbar">
              <span
                className="detbarfill"
                style={{ width: `${Math.round(d.score * 100)}%`, background: classColor(d.class) }}
              />
            </span>
          </div>
        ))}
      </div>
    </aside>
  );
}

export function StatusPanel({ tlm, det }) {
  // Newest first, and only the handful that fit.
  const events = [...(tlm?.events ?? [])].reverse().slice(0, 6);

  return (
    <aside className="sidepanel">
      <div className="panel-head">
        <span>Status</span>
      </div>

      <div className="panel-stats">
        <StatRow
          label="FC link"
          value={tlm?.hb_age_ms != null ? tlm.hb_age_ms : "--"}
          unit=" ms"
          cls={ageCls(tlm?.hb_age_ms, 1500)}
        />
        <StatRow
          label="Uplink"
          value={tlm?.ctrl_age_ms != null ? tlm.ctrl_age_ms : "--"}
          unit=" ms"
          cls={ageCls(tlm?.ctrl_age_ms, 200)}
        />
        <StatRow
          label="Detector"
          value={det ? "live" : "offline"}
          cls={det ? "g" : "x"}
        />
        <StatRow
          label="EKF"
          value={tlm ? (tlm.ekf_ok ? "ok" : "check") : "--"}
          cls={tlm ? (tlm.ekf_ok ? "g" : "y") : "x"}
        />
        <StatRow
          label="GPS"
          value={tlm ? `${tlm.sats} sat` : "--"}
          cls={tlm ? (tlm.gps_fix >= 3 ? "g" : "y") : "x"}
        />
      </div>

      <div className="panel-head panel-head-sub">
        <span>Events</span>
      </div>
      <div className="panel-body">
        {events.length === 0 && <div className="panel-empty">no events yet</div>}
        {events.map((e, i) => (
          <div className={`evtrow evt-${e.kind}`} key={`${e.ts}-${i}`}>
            <span className="evtkind">{e.kind}</span>
            <span className="evtmsg">{e.msg}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}
