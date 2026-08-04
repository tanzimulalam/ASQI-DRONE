import { useState } from "react";
import { useDroneLink } from "./hooks/useDroneLink.js";
import TelemetryBar from "./components/TelemetryBar.jsx";
import FailsafeBanner from "./components/FailsafeBanner.jsx";
import TakeoverBanner from "./components/TakeoverBanner.jsx";
import Stick from "./components/Stick.jsx";
import VideoPanel from "./components/VideoPanel.jsx";
import CommandDeck from "./components/CommandDeck.jsx";
import TakeoffModal from "./components/TakeoffModal.jsx";
import LoginOverlay from "./components/LoginOverlay.jsx";

export default function App() {
  const { axesRef, tlm, det, conn, sendCmd, authed, authBusy, authError, airborneIp, login } =
    useDroneLink();
  const [takeoffOpen, setTakeoffOpen] = useState(false);

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
        />
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
