/**
 * The command deck: mode buttons + RTL, and ARM & TAKE OFF / DISARM.
 *
 * The active mode is highlighted from live telemetry. Modes the aircraft will
 * accept stick overrides in (`allowed_modes`, reported by the airborne daemon)
 * carry a marker — in anything else the daemon refuses overrides outright, and
 * without this the operator only discovers that by pushing a stick and watching
 * nothing happen.
 */

function ModeButton({ mode, label, current, allowed, onSetMode }) {
  const active = current === mode;
  const canPilot = allowed.includes(mode);
  return (
    <button
      className={`cmd modebtn${active ? " active" : ""}`}
      onClick={() => onSetMode(mode)}
      title={canPilot ? "Stick control available in this mode" : undefined}
    >
      {canPilot && <span className="pilotable" aria-hidden="true" />}
      {label}
    </button>
  );
}

export default function CommandDeck({
  mode,
  allowedModes,
  armed,
  onSetMode,
  onArm,
  onDisarm,
  onTakeoff,
}) {
  const allowed = allowedModes ?? [];

  return (
    <div id="deck">
      <div className="row">
        <ModeButton
          mode="LOITER"
          label="Loiter"
          current={mode}
          allowed={allowed}
          onSetMode={onSetMode}
        />
        <ModeButton
          mode="ALT_HOLD"
          label="Alt Hold"
          current={mode}
          allowed={allowed}
          onSetMode={onSetMode}
        />
        <button className="cmd" id="btnRTL" onClick={() => onSetMode("RTL")}>
          RTL
        </button>
      </div>
      <div className="row">
        {/* Plain arm, no climb. Bench testing with the props off needs motors at
            idle: ARM & TAKE OFF commands a climb, and with no thrust to show for
            it the FC keeps pushing the throttle up, spinning unloaded motors
            toward full RPM. */}
        <button className="cmd" id="btnArm" onClick={onArm} disabled={armed}>
          Arm&nbsp;Only
        </button>
        <button className="cmd" id="btnTakeoff" onClick={onTakeoff}>
          Arm &amp; Take Off
        </button>
        <button className="cmd" id="btnDisarm" onClick={onDisarm}>
          Disarm
        </button>
      </div>
    </div>
  );
}
