import { useCallback, useEffect, useRef, useState } from "react";
import { useDroneLink } from "./hooks/useDroneLink.js";
import { useKeyboardControl } from "./hooks/useKeyboardControl.js";
import TelemetryBar from "./components/TelemetryBar.jsx";
import FailsafeBanner from "./components/FailsafeBanner.jsx";
import PreArmBanner from "./components/PreArmBanner.jsx";
import ControlBanner from "./components/ControlBanner.jsx";
import TakeoverBanner from "./components/TakeoverBanner.jsx";
import Stick from "./components/Stick.jsx";
import VideoPanel from "./components/VideoPanel.jsx";
import CommandDeck from "./components/CommandDeck.jsx";
import ControlMode from "./components/ControlMode.jsx";
import { VisionPanel, StatusPanel } from "./components/SidePanels.jsx";
import TakeoffModal from "./components/TakeoffModal.jsx";
import FleetScreen from "./components/FleetScreen.jsx";
import HeightDeck from "./components/HeightDeck.jsx";
import { useAltitudeHold } from "./hooks/useAltitudeHold.js";
import { HOLD_HEIGHTS_M } from "./config.js";

const NO_KEYS = new Set();
const THROTTLE_OWNED = new Set(["throttle"]);
const NO_AXES_OWNED = new Set();

/**
 * Cockpit layout: the feed spans the top, and the controls sit in a band beneath
 * it with a stick under each thumb and the command console between them. Putting
 * the video above rather than between the sticks lets it use the full width,
 * which is what a 4:3 feed on a landscape panel wants.
 */
export default function App() {
  const {
    axesRef,
    tlm,
    det,
    conn,
    sendCmd,
    authed,
    authBusy,
    authError,
    airborneIp,
    airborneName,
    login,
    logout,
    txOk,
  } = useDroneLink();
  const [takeoffOpen, setTakeoffOpen] = useState(false);
  // "touch" (on-screen gimbals) or "keyboard" (WASD + numpad). Exactly one owns
  // the axes at a time; see ControlMode.
  const [controlMode, setControlMode] = useState("touch");
  const [heldKeys, setHeldKeys] = useState(NO_KEYS);
  // Height the aircraft is being held at, or null when the operator is flying
  // the throttle by hand.
  const [holdTarget, setHoldTarget] = useState(null);
  const [holdNote, setHoldNote] = useState(null);
  // When telemetry last arrived. The link hook hands out the latest snapshot but
  // no timestamp, and the height hold has to know the difference between "level
  // flight" and "nothing has arrived for two seconds".
  const tlmAtRef = useRef(0);
  useEffect(() => {
    if (tlm) tlmAtRef.current = Date.now();
  }, [tlm]);

  // Suspend keyboard piloting while the takeoff dialog is up: it owns the
  // altitude field, and a stray W there should type, not climb.
  const keyboardActive = authed && controlMode === "keyboard" && !takeoffOpen;
  const onKeysChange = useCallback((keys) => setHeldKeys(keys), []);
  useKeyboardControl(
    axesRef,
    keyboardActive,
    onKeysChange,
    holdTarget != null ? THROTTLE_OWNED : NO_AXES_OWNED,
  );

  // The hold gives the axis back the moment anything changes underneath it.
  const cancelHold = useCallback((why) => {
    setHoldTarget(null);
    setHoldNote(why || null);
  }, []);
  useAltitudeHold(axesRef, { target: holdTarget, tlm, tlmAt: tlmAtRef.current, onCancel: cancelHold });

  // Touching the throttle by hand means the operator wants it back.
  useEffect(() => {
    if (holdTarget != null && (heldKeys.has("KeyW") || heldKeys.has("KeyS"))) {
      cancelHold("manual throttle");
    }
  }, [heldKeys, holdTarget, cancelHold]);

  const land = useCallback(() => {
    setHoldTarget(null);
    setHoldNote("landing");
    sendCmd({ t: "cmd", cmd: "set_mode", mode: "LAND" });
  }, [sendCmd]);

  // Number keys pick a height; backtick lands. Ignored while typing, and while
  // the takeoff dialog owns the keyboard.
  useEffect(() => {
    if (!authed || takeoffOpen) return undefined;
    const onKey = (e) => {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.code === "Backquote") {
        e.preventDefault();
        land();
        return;
      }
      const m = /^Digit([1-6])$/.exec(e.code);
      if (!m) return;
      e.preventDefault();
      const height = HOLD_HEIGHTS_M[Number(m[1]) - 1];
      if (height != null) {
        setHoldNote(null);
        setHoldTarget(height);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [authed, takeoffOpen, land]);

  // Gate the whole cockpit behind login: no sticks, no telemetry, no video until
  // the operator picks an aircraft on the fleet screen and its password
  // authenticates to it.
  if (!authed) {
    return (
      <FleetScreen
        wsReady={conn.cls !== "x" && conn.label !== "BRIDGE LOST"}
        busy={authBusy}
        error={authError}
        onLogin={login}
      />
    );
  }

  const setMode = (mode) => sendCmd({ t: "cmd", cmd: "set_mode", mode });
  const arm = () => sendCmd({ t: "cmd", cmd: "arm" });
  const disarm = () => sendCmd({ t: "cmd", cmd: "disarm" });
  const resume = () => sendCmd({ t: "cmd", cmd: "resume" });
  const confirmTakeoff = (alt) => {
    sendCmd({ t: "cmd", cmd: "takeoff", alt });
    setTakeoffOpen(false);
  };

  const lastEvent = tlm?.events?.length ? tlm.events[tlm.events.length - 1] : null;
  const sticksOff = controlMode === "keyboard";

  return (
    <div id="app">
      <TelemetryBar
        tlm={tlm}
        conn={conn}
        airborneIp={airborneIp}
        airborneName={airborneName}
        onFleet={logout}
      />
      <ControlBanner tlm={tlm} txOk={txOk} />
      <PreArmBanner tlm={tlm} />
      <FailsafeBanner active={!!tlm?.failsafe} onResume={resume} />
      <TakeoverBanner active={!tlm?.failsafe && !!tlm?.pilot_takeover} onResume={resume} />

      <div id="stage">
        <div className="videostage">
          <VisionPanel det={det} />
          <VideoPanel det={det} />
          <StatusPanel tlm={tlm} det={det} />
        </div>

        <div className="controlrow">
          {/* LEFT: throttle (Y) + yaw (X) — self-centering, center = hold */}
          <Stick
            axesRef={axesRef}
            axisX="yaw"
            axisY="throttle"
            hint="Throttle ↕ · Yaw ↔"
            disabled={sticksOff}
          />

          <div className="console">
            <CommandDeck
              mode={tlm?.mode}
              allowedModes={tlm?.allowed_modes}
              armed={!!tlm?.armed}
              onSetMode={setMode}
              onArm={arm}
              onDisarm={disarm}
              onTakeoff={() => setTakeoffOpen(true)}
            />
            <HeightDeck
              target={holdTarget}
              alt={tlm?.alt}
              onPick={(h) => {
                setHoldNote(null);
                setHoldTarget(h);
              }}
              onLand={land}
              disabled={!tlm?.armed}
            />
            <ControlMode
              mode={controlMode}
              onChange={setControlMode}
              held={keyboardActive ? heldKeys : NO_KEYS}
            />
          </div>

          {/* RIGHT: pitch (Y) + roll (X) — self-centering */}
          <Stick
            axesRef={axesRef}
            axisX="roll"
            axisY="pitch"
            hint="Pitch ↕ · Roll ↔"
            disabled={sticksOff}
          />
        </div>
      </div>

      <div id="evt">{holdNote ? `height hold: ${holdNote}` : lastEvent ? `${lastEvent.kind}: ${lastEvent.msg}` : ""}</div>

      <TakeoffModal
        open={takeoffOpen}
        axesRef={axesRef}
        onConfirm={confirmTakeoff}
        onCancel={() => setTakeoffOpen(false)}
      />
    </div>
  );
}
