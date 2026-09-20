import { useCallback, useState } from "react";
import { useDroneLink } from "./hooks/useDroneLink.js";
import { useKeyboardControl } from "./hooks/useKeyboardControl.js";
import TelemetryBar from "./components/TelemetryBar.jsx";
import FailsafeBanner from "./components/FailsafeBanner.jsx";
import ControlBanner from "./components/ControlBanner.jsx";
import TakeoverBanner from "./components/TakeoverBanner.jsx";
import Stick from "./components/Stick.jsx";
import VideoPanel from "./components/VideoPanel.jsx";
import CommandDeck from "./components/CommandDeck.jsx";
import ControlMode from "./components/ControlMode.jsx";
import { VisionPanel, StatusPanel } from "./components/SidePanels.jsx";
import TakeoffModal from "./components/TakeoffModal.jsx";
import LoginOverlay from "./components/LoginOverlay.jsx";

const NO_KEYS = new Set();

/**
 * Cockpit layout: the feed spans the top, and the controls sit in a band beneath
 * it with a stick under each thumb and the command console between them. Putting
 * the video above rather than between the sticks lets it use the full width,
 * which is what a 4:3 feed on a landscape panel wants.
 */
export default function App() {
  const { axesRef, tlm, det, conn, sendCmd, authed, authBusy, authError, airborneIp, login, txOk } =
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
      <TelemetryBar tlm={tlm} conn={conn} airborneIp={airborneIp} />
      <ControlBanner tlm={tlm} txOk={txOk} />
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
