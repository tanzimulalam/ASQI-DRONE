function Dot({ cls }) {
  return <span className={`dot ${cls}`} />;
}

function Chip({ k, children }) {
  return (
    <div className="chip">
      <span className="k">{k}</span>
      <span className="v">{children}</span>
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
 * The wrapping telemetry chip row. `tlm` is the latest telemetry object or null.
 * `conn` is the coarse link state used before/without telemetry.
 */
export default function TelemetryBar({ tlm, conn, airborneIp }) {
  const armed = !!tlm?.armed;
  // Arm chip: telemetry (armed/disarmed) wins; otherwise show link state.
  let armCls, armLabel;
  if (tlm) {
    armCls = armed ? "r" : "g";
    armLabel = armed ? "● ARMED" : "DISARMED";
  } else {
    armCls = conn.cls;
    armLabel = conn.label;
  }

  const ctrl = ageParts(tlm?.ctrl_age_ms);
  const hb = ageParts(tlm?.hb_age_ms, 1500);

  return (
    <div id="topbar">
      <div className={`chip${armed ? " armed" : ""}`} id="armchip">
        <span className="v">
          <Dot cls={armCls} />
          {armLabel}
        </span>
      </div>
      <Chip k="Mode">{tlm?.mode || "--"}</Chip>
      <Chip k="GPS">{tlm ? `${tlm.sats} sat · fix ${tlm.gps_fix}` : "--"}</Chip>
      <Chip k="Batt">
        {tlm ? `${tlm.batt_v} V${tlm.batt_pct >= 0 ? "  " + tlm.batt_pct + "%" : ""}` : "--"}
      </Chip>
      <Chip k="Cur">{tlm ? `${tlm.current_a} A` : "--"}</Chip>
      <Chip k="Alt">{tlm ? `${tlm.alt} m` : "--"}</Chip>
      <Chip k="Spd">{tlm ? `${tlm.gspeed} m/s` : "--"}</Chip>
      <Chip k="EKF">
        {tlm ? (
          <>
            <Dot cls={tlm.ekf_ok ? "g" : "y"} />
            {tlm.ekf_ok ? "OK" : "CHK"}
          </>
        ) : (
          "--"
        )}
      </Chip>
      <Chip k="Ctrl">
        <Dot cls={ctrl.cls} />
        {ctrl.text}
      </Chip>
      <Chip k="FC HB">
        <Dot cls={hb.cls} />
        {hb.text}
      </Chip>
      <Chip k="Link">{tlm?.link_phase || "--"}</Chip>
      {airborneIp && <Chip k="Drone">{airborneIp}</Chip>}
    </div>
  );
}
