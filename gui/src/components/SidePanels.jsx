/**
 * The flanking panels either side of the feed.
 *
 * A 4:3 camera on a 16:9 panel always leaves space to the left and right of the
 * video. Rather than leave it black, it carries the two things an operator would
 * otherwise have to read off the video itself: what the detector currently sees,
 * and what the aircraft has recently done.
 *
 * Both collapse away on narrow screens (see styles.css) where the space is
 * needed by the feed.
 */

// Same hue-from-label hash VideoPanel uses, so a class keeps one colour in both
// the box overlay and this list.
function classColor(label) {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
  return `hsl(${h}, 85%, 60%)`;
}

export function DetectionList({ det }) {
  const boxes = det?.boxes ?? [];
  // Biggest score first; the detector already sorts, but don't rely on it.
  const rows = [...boxes].sort((a, b) => b.score - a.score).slice(0, 8);

  return (
    <aside className="sidepanel">
      <div className="panel-head">
        <span>Detections</span>
        {det && <span className="panel-count">{boxes.length}</span>}
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

export function EventLog({ tlm }) {
  // Newest first, and only the handful that fit.
  const events = [...(tlm?.events ?? [])].reverse().slice(0, 8);

  return (
    <aside className="sidepanel">
      <div className="panel-head">
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
