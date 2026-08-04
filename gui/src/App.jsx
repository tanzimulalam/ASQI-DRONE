import { useCallback, useState } from "react";
import { useDroneLink } from "./hooks/useDroneLink.js";
import { useKeyboardControl } from "./hooks/useKeyboardControl.js";
import TelemetryBar from "./components/TelemetryBar.jsx";
import FailsafeBanner from "./components/FailsafeBanner.jsx";
import TakeoverBanner from "./components/TakeoverBanner.jsx";
import Stick from "./components/Stick.jsx";
import VideoPanel from "./components/VideoPanel.jsx";
import CommandDeck from "./components/CommandDeck.jsx";
import ControlMode from "./components/ControlMode.jsx";
import TakeoffModal from "./components/TakeoffModal.jsx";
import LoginOverlay from "./components/LoginOverlay.jsx";

const NO_KEYS = new Set();

export default function App() {
  const { axesRef, tlm, det, conn, sendCmd, authed, authBusy, authError, airborneIp, login } =
    useDroneLink();
  const [takeoffOpen, setTakeoffOpen] = useState(false);
  // "touch" (on-screen gimbals) or "keyboard" (WASD + numpad). Exactly one owns
  // the axes at a time; see ControlMode.
  const [controlMode, setControlMode] = useState("touch");
  const [heldKeys, setHeldKeys] = useState(NO_KEYS);

  // Suspend keyboard piloting while the takeoff dialog is up: it owns the
  // altitude field, and a stray W there should type, not climb.
  const keyboardActive = authed && controlMode === "keyboard" && !takeoffOpen;
  const onKeysChange = useCallback((keys) => setHeldKeys(keys), []);
  useKeyboardControl(axesRef, keyboardActive, onKeysChange);

  // Gate the whole cockpit behind login: no sticks, no telemetry, no video until
  // the password authenticates to a drone.
  if (!authed) {
    return (
      <LoginOverlay
        wsReady={conn.cls !== "x" && conn.label !== "BRIDGE LOST"}
        busy={authBusy}
        error={authError}
        onSubmit={login}
      />
    );
  }

  const setMode = (mode) => sendCmd({ t: "cmd", cmd: "set_mode", mode });
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
      <TelemetryBar tlm={tlm} conn={conn} airborneIp={airborneIp} />
      <FailsafeBanner active={!!tlm?.failsafe} onResume={resume} />
      <TakeoverBanner active={!tlm?.failsafe && !!tlm?.pilot_takeover} onResume={resume} />

      <div id="stage">
        {/* LEFT: throttle (Y) + yaw (X) — self-centering, center = hold */}
        <Stick
          axesRef={axesRef}
          axisX="yaw"
          axisY="throttle"
          hint="Throttle ↕ · Yaw ↔ — center = hold"
          disabled={sticksOff}
        />

        <div className="center">
          <VideoPanel det={det} />
          <CommandDeck
            mode={tlm?.mode}
            onSetMode={setMode}
            onDisarm={disarm}
            onTakeoff={() => setTakeoffOpen(true)}
          />
        </div>

        {/* RIGHT: pitch (Y) + roll (X) — self-centering */}
        <Stick
          axesRef={axesRef}
          axisX="roll"
          axisY="pitch"
          hint="Pitch ↕ · Roll ↔ — self-centering"
          disabled={sticksOff}
        />
      </div>

      <ControlMode
        mode={controlMode}
        onChange={setControlMode}
        held={keyboardActive ? heldKeys : NO_KEYS}
      />

      <div id="evt">{lastEvent ? `${lastEvent.kind}: ${lastEvent.msg}` : ""}</div>

      <TakeoffModal
        open={takeoffOpen}
        axesRef={axesRef}
        onConfirm={confirmTakeoff}
        onCancel={() => setTakeoffOpen(false)}
      />
    </div>
  );
}
