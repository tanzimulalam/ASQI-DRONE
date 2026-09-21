import Brand from "./Brand.jsx";

function Dot({ cls }) {
  return <span className={`dot ${cls}`} />;
}

/**
 * A telemetry chip.
 *
 * `drop` marks a chip as expendable when the row runs out of room: the band
 * cannot wrap, so on a narrow screen the least critical readings are hidden
 * rather than letting the tail (including the lab identity) get clipped.
 * Lower numbers go first. See the topbar media queries in styles.css.
 *
 * `w` reserves the value's width in `ch` units, sized for the widest string the
 * field can ever hold. Without it a chip grows when its number does — "30 ms"
 * becoming "1234 ms" widened the row, wrapped the bar onto a second line, and
 * resized the video underneath. Reserving the space means the bar never reflows
 * no matter what the numbers do.
 */
function Chip({ k, w, drop, fill, children }) {
  return (
    <div className={`chip${drop ? ` drop-${drop}` : ""}`}>
      <span className="k">{k}</span>
      <span className="v" style={w ? { minWidth: `${w}ch` } : undefined}>
        {children}
      </span>
      {fill != null && (
        <span className="chipgauge">
          <span
            className={`chipgaugefill${fill <= 20 ? " low" : fill <= 40 ? " mid" : ""}`}
            style={{ width: `${Math.max(0, Math.min(100, fill))}%` }}
          />
        </span>
      )}
    </div>
  );
}

// Colored freshness marker for an age in ms; grey/-- when unknown.
function ageParts(ms, warn = 200) {
  if (ms == null || ms < 0) return { cls: "x", text: "--" };
  const cls = ms > warn * 3 ? "r" : ms > warn ? "y" : "g";
  return { cls, text: `${ms} ms` };
}

/**
 * The telemetry chip row. `tlm` is the latest telemetry object or null.
 * `conn` is the coarse link state used before/without telemetry.
 */
export default function TelemetryBar({ tlm, conn, airborneIp, airborneName, onFleet }) {
  const armed = !!tlm?.armed;
  // Arm chip: telemetry (armed/disarmed) wins; otherwise show link state.
  let armCls, armLabel;
  if (tlm) {
    armCls = armed ? "r" : "g";
    armLabel = armed ? "ARMED" : "DISARMED";
  } else {
    armCls = conn.cls;
    armLabel = conn.label;
  }

  // Under a volt is no flight battery at all (bench, Jetson on wall power), not a
  // flat pack. Matches the fleet screen, so both views tell the same story.
  const noBatt = tlm?.batt_v != null && tlm.batt_v < 1.0;

  const ctrl = ageParts(tlm?.ctrl_age_ms);
  const hb = ageParts(tlm?.hb_age_ms, 1500);

  return (
    <div id="topbar">
      {/* Back to the fleet. Disabled while armed: leaving ends the control
          session, and an armed aircraft is exactly when that must not happen by
          accident. Uses the last known arm state, so a link that drops while
          armed keeps the button locked rather than freeing it. */}
      {onFleet && (
        <button
          type="button"
          className="fleetback"
          onClick={onFleet}
          disabled={armed}
          title={armed ? "Disarm before returning to the fleet" : "Return to the fleet screen"}
        >
          <span className="fleetback-arrow" aria-hidden="true">‹</span>
          <span className="fleetback-label">{armed ? "Armed" : "Fleet"}</span>
        </button>
      )}

      <div className={`chip${armed ? " armed" : ""}`} id="armchip">
        <span className="v">
          <Dot cls={armCls} />
          {armLabel}
        </span>
      </div>

      {/* Which aircraft this cockpit commands. Never dropped on narrow screens:
          with more than one airframe, not knowing which one you are flying is the
          failure worth designing against. */}
      {airborneName && <Chip k="Aircraft" w={9}>{airborneName}</Chip>}

      <Chip k="Mode" w={9}>{tlm?.mode || "--"}</Chip>
      <Chip k="GPS" w={13}>{tlm ? `${tlm.sats} sat · fix ${tlm.gps_fix}` : "--"}</Chip>
      <Chip k="Batt" w={11} fill={noBatt ? null : tlm && tlm.batt_pct >= 0 ? tlm.batt_pct : null}>
        {!tlm
          ? "--"
          : noBatt
            ? "none"
            : `${tlm.batt_v} V${tlm.batt_pct >= 0 ? "  " + tlm.batt_pct + "%" : ""}`}
      </Chip>
      <Chip k="Cur" w={7} drop={1}>{tlm ? `${tlm.current_a} A` : "--"}</Chip>
      <Chip k="Alt" w={8}>{tlm ? `${tlm.alt} m` : "--"}</Chip>
      <Chip k="Spd" w={9} drop={2}>{tlm ? `${tlm.gspeed} m/s` : "--"}</Chip>
      <Chip k="EKF" w={6}>
        {tlm ? (
          <>
            <Dot cls={tlm.ekf_ok ? "g" : "y"} />
            {tlm.ekf_ok ? "OK" : "CHK"}
          </>
        ) : (
          "--"
        )}
      </Chip>
      <Chip k="Ctrl" w={8}>
        <Dot cls={ctrl.cls} />
        {ctrl.text}
      </Chip>
      <Chip k="FC HB" w={8}>
        <Dot cls={hb.cls} />
        {hb.text}
      </Chip>
      <Chip k="Link" w={8} drop={2}>{tlm?.link_phase || "--"}</Chip>
      {airborneIp && <Chip k="Drone" w={15} drop={1}>{airborneIp}</Chip>}

      <Brand />
    </div>
  );
}
