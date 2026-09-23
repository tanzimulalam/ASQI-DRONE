import { HOLD_HEIGHTS_M } from "../config.js";

/**
 * Height presets: what the number keys do, and what is happening now.
 *
 * Shown always, so the keys are discoverable rather than folklore, and so the
 * operator can click them instead of reaching for the keyboard. The active
 * target is highlighted, and the live height is next to it, because "did it
 * actually go there" is the question this panel exists to answer.
 */
export default function HeightDeck({ target, alt, onPick, onLand, disabled }) {
  const shown = Number.isFinite(alt) ? alt.toFixed(1) : "--";
  return (
    <div className="heightdeck">
      <span className="hd-label">Height</span>
      {HOLD_HEIGHTS_M.map((h, i) => (
        <button
          key={h}
          className={`hd-key${target === h ? " on" : ""}`}
          onClick={() => onPick(h)}
          disabled={disabled}
          title={`${h.toFixed(1)} m`}
        >
          <span className="hd-num">{i + 1}</span>
          <span className="hd-m">{h.toFixed(1)}</span>
        </button>
      ))}
      <button className="hd-land" onClick={onLand} title="LAND (backtick)">
        <span className="hd-num">`</span>
        <span className="hd-m">land</span>
      </button>
      <span className={`hd-now${target != null ? " on" : ""}`}>
        {target != null ? `holding ${target.toFixed(1)} m` : "manual"}
        <span className="hd-alt">alt {shown} m</span>
      </span>
    </div>
  );
}
