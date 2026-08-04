/**
 * The command deck: mode buttons + RTL, and ARM & TAKE OFF / DISARM.
 * The active mode button is highlighted from live telemetry.
 */
export default function CommandDeck({ mode, onSetMode, onDisarm, onTakeoff }) {
  return (
    <div id="deck">
      <div className="row">
        <button
          className={`cmd modebtn${mode === "LOITER" ? " active" : ""}`}
          onClick={() => onSetMode("LOITER")}
        >
          LOITER
        </button>
        <button
          className={`cmd modebtn${mode === "ALT_HOLD" ? " active" : ""}`}
          onClick={() => onSetMode("ALT_HOLD")}
        >
          ALT&nbsp;HOLD
        </button>
        <button className="cmd" id="btnRTL" onClick={() => onSetMode("RTL")}>
          RTL
        </button>
      </div>
      <div className="row">
        <button className="cmd" id="btnTakeoff" onClick={onTakeoff}>
          ARM&nbsp;&amp;&nbsp;TAKE&nbsp;OFF
        </button>
        <button className="cmd" id="btnDisarm" onClick={onDisarm}>
          DISARM
        </button>
      </div>
    </div>
  );
}
