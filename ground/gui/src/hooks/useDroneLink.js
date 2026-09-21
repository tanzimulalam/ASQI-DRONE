import { useEffect, useRef, useState, useCallback } from "react";
import { CTRL_PERIOD_MS, TLM_STALE_MS, DET_STALE_MS, TX_STALL_MS } from "../config.js";

/**
 * A 50 Hz tick that a hidden tab cannot silence.
 *
 * Browsers clamp setInterval in backgrounded tabs to roughly 1 Hz. Measured on
 * 2026-08-06 with the cockpit tab hidden: 0 to 3 control packets in 2 seconds,
 * against the 50 Hz this link requires. The gap between packets then exceeds the
 * airborne 600 ms failsafe threshold, so the aircraft neutralises the sticks and
 * commands LAND while the radio is perfectly healthy. Tab switched, window
 * minimised, screen locked or another workspace all do it.
 *
 * Worker timers are not subject to that clamp, so the tick is generated there and
 * the send still happens on the main thread, which owns the socket. If a Worker
 * cannot be constructed we fall back to setInterval, which is no worse than the
 * behaviour this replaces.
 *
 * This does not survive the whole page being frozen, which stops workers too.
 * That is why the caller also tracks whether packets are genuinely leaving and
 * says so in the UI rather than assuming this worked.
 */
function startTicker(periodMs, onTick) {
  try {
    const src =
      "let id=null;onmessage=function(e){var m=e.data||{};" +
      "if(m.t==='start'){clearInterval(id);id=setInterval(function(){postMessage(0);},m.ms);}" +
      "else if(m.t==='stop'){clearInterval(id);id=null;}};";
    const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
    const worker = new Worker(url);
    worker.onmessage = onTick;
    worker.postMessage({ t: "start", ms: periodMs });
    return {
      kind: "worker",
      stop() {
        try {
          worker.postMessage({ t: "stop" });
          worker.terminate();
        } catch {
          /* already gone */
        }
        URL.revokeObjectURL(url);
      },
    };
  } catch {
    const id = setInterval(onTick, periodMs);
    return { kind: "interval", stop: () => clearInterval(id) };
  }
}

/** Login packet. The drone field is only sent when a specific aircraft was chosen. */
function authPacket(password, drone) {
  return drone ? { t: "auth", password, drone } : { t: "auth", password };
}

/**
 * Owns the WebSocket to the ground bridge, the login handshake, the 50 Hz control
 * uplink, and the telemetry downlink.
 *
 * Auth: the bridge relays nothing and sends no telemetry until the operator's
 * password (the drone token) is accepted. `login(password)` sends an auth packet;
 * the bridge probes its candidate drone IPs and replies auth_ok / auth_fail. The
 * password is kept in a ref so a dropped link silently re-authenticates on
 * reconnect; a rejected password is discarded so we don't loop on a bad one.
 *
 * Performance contract (unchanged): stick motion must NOT re-render React. Axis
 * values live in `axesRef` and are read by the sender; the sender only transmits
 * once authenticated.
 *
 * Returns: axesRef, tlm, conn, sendCmd, txOk, and { authed, authBusy, authError,
 * airborneIp, login }.
 */
export function useDroneLink() {
  const axesRef = useRef({ roll: 0, pitch: 0, throttle: 0, yaw: 0 });
  const wsRef = useRef(null);
  const openRef = useRef(false);
  const authedRef = useRef(false);
  const passwordRef = useRef(null);
  // Which aircraft the password belongs to. Kept alongside the password so a
  // reconnect after a network blip goes back to the SAME aircraft, rather than
  // letting the bridge probe every candidate and connect to whichever answers.
  const droneRef = useRef(null);
  const seqRef = useRef(0);
  const lastTlmRef = useRef(0);
  // When a control packet last actually left this browser. Drives txOk, which is
  // the cockpit's honest answer to "are we still flying this thing".
  const lastSendRef = useRef(0);
  const txOkRef = useRef(true);

  const [tlm, setTlm] = useState(null);
  // Newest detection frame from the ground-side TensorRT detector, or null when
  // the detector is absent or has gone quiet. Boxes are drawn by VideoPanel.
  const [det, setDet] = useState(null);
  const lastDetRef = useRef(0);
  const [conn, setConn] = useState({ label: "CONNECTING", cls: "x" });
  const [authed, setAuthed] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState(null);
  const [airborneIp, setAirborneIp] = useState(null);
  // Fleet name of the aircraft this session is flying, e.g. "Piper". Shown in
  // the cockpit so the operator can never be unsure which aircraft they command.
  const [airborneName, setAirborneName] = useState(null);
  const [txOk, setTxOk] = useState(true);

  const setAuthedBoth = (v) => {
    authedRef.current = v;
    setAuthed(v);
  };

  const sendRaw = useCallback((obj) => {
    const ws = wsRef.current;
    if (openRef.current && ws) {
      try {
        ws.send(JSON.stringify(obj));
      } catch {
        /* socket closing; drop */
      }
    }
  }, []);

  // command packets only make sense once authenticated
  const sendCmd = useCallback(
    (obj) => {
      if (authedRef.current) sendRaw(obj);
    },
    [sendRaw]
  );

  // `drone` is the fleet name to log into. Omitting it keeps the original
  // behaviour: the bridge probes every candidate and whichever accepts connects.
  const login = useCallback(
    (password, drone = null) => {
      passwordRef.current = password;
      droneRef.current = drone;
      setAuthError(null);
      setAuthBusy(true);
      if (openRef.current) sendRaw(authPacket(password, drone));
      // if the socket isn't open yet, onopen will send it
    },
    [sendRaw]
  );

  useEffect(() => {
    let backoff = 500;
    let reconnectTimer = 0;
    let disposed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      wsRef.current = ws;

      ws.onopen = () => {
        openRef.current = true;
        backoff = 500;
        setConn({ label: "CONNECTED", cls: "y" });
        // silently re-authenticate across reconnects if we have a password, to
        // the same aircraft as before
        if (passwordRef.current) {
          setAuthBusy(true);
          ws.send(JSON.stringify(authPacket(passwordRef.current, droneRef.current)));
        }
      };
      ws.onclose = () => {
        openRef.current = false;
        setAuthedBoth(false);
        setAuthBusy(false);
        if (disposed) return;
        setConn({ label: "BRIDGE LOST", cls: "r" });
        reconnectTimer = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 5000);
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
      ws.onmessage = (e) => {
        let msg;
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        switch (msg.t) {
          case "auth_ok":
            lastSendRef.current = Date.now(); // don't flag a stall before the first send
            setAuthedBoth(true);
            setAuthBusy(false);
            setAuthError(null);
            setAirborneIp(msg.airborne ?? null);
            setAirborneName(msg.drone ?? null);
            break;
          case "auth_fail":
            setAuthedBoth(false);
            setAuthBusy(false);
            setAuthError(msg.reason || "password rejected");
            passwordRef.current = null; // don't auto-retry a bad password
            droneRef.current = null;
            break;
          case "auth_required":
            setAuthedBoth(false);
            setAuthBusy(false);
            break;
          case "tlm":
            lastTlmRef.current = Date.now();
            setTlm(msg);
            break;
          case "det":
            lastDetRef.current = Date.now();
            setDet(msg);
            break;
          default:
            break;
        }
      };
    };

    connect();

    // 50 Hz control uplink, only transmitted once authenticated. Driven from a
    // worker so a hidden tab cannot throttle it; see startTicker above.
    //
    // A hidden tab keeps transmitting rather than going silent. The axes are
    // already zeroed by the keyboard interlock on blur or page-hide, so what goes
    // out is neutral sticks, which the aircraft holds on. That is deliberate: the
    // 600 ms failsafe exists to detect a lost radio link, and letting a browser
    // timer optimisation impersonate one turns every alt-tab into a LAND command.
    let ticks = 0;
    const checkEvery = Math.max(1, Math.round(200 / CTRL_PERIOD_MS));

    const onTick = () => {
      const now = Date.now();
      if (openRef.current && authedRef.current) {
        const a = axesRef.current;
        try {
          wsRef.current.send(
            JSON.stringify({
              t: "ctrl",
              seq: seqRef.current++,
              ts: now,
              roll: +a.roll.toFixed(4),
              pitch: +a.pitch.toFixed(4),
              thr: +a.throttle.toFixed(4),
              yaw: +a.yaw.toFixed(4),
            })
          );
          lastSendRef.current = now;
        } catch {
          /* socket closing; the stall check below will notice */
        }
      }
      // Evaluated a few times a second rather than every tick: this runs at 50 Hz
      // and setState here would re-render React on every stick frame, which the
      // performance contract above forbids.
      if (++ticks % checkEvery === 0) {
        const live = !authedRef.current || now - lastSendRef.current < TX_STALL_MS;
        if (live !== txOkRef.current) {
          txOkRef.current = live;
          setTxOk(live);
        }
      }
    };

    const ticker = startTicker(CTRL_PERIOD_MS, onTick);

    // stale-telemetry watchdog (only meaningful once connected to a drone)
    const staleTimer = setInterval(() => {
      if (authedRef.current && Date.now() - lastTlmRef.current > TLM_STALE_MS) {
        setConn({ label: "NO TELEMETRY", cls: "r" });
      }
      // Drop the overlay when detections stop: boxes frozen over moving video
      // claim things about the scene that are no longer true.
      if (lastDetRef.current && Date.now() - lastDetRef.current > DET_STALE_MS) {
        lastDetRef.current = 0;
        setDet(null);
      }
    }, 500);

    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      ticker.stop();
      clearInterval(staleTimer);
      try {
        wsRef.current?.close();
      } catch {
        /* ignore */
      }
    };
  }, []);

  return {
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
    txOk,
  };
}
