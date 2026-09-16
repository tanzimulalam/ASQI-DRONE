import { useEffect, useRef, useState, useCallback } from "react";
import { CTRL_PERIOD_MS, TLM_STALE_MS, DET_STALE_MS } from "../config.js";

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
 * Returns: axesRef, tlm, conn, sendCmd, and { authed, authBusy, authError,
 * airborneIp, login }.
 */
export function useDroneLink() {
  const axesRef = useRef({ roll: 0, pitch: 0, throttle: 0, yaw: 0 });
  const wsRef = useRef(null);
  const openRef = useRef(false);
  const authedRef = useRef(false);
  const passwordRef = useRef(null);
  const seqRef = useRef(0);
  const lastTlmRef = useRef(0);

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

  const login = useCallback(
    (password) => {
      passwordRef.current = password;
      setAuthError(null);
      setAuthBusy(true);
      if (openRef.current) sendRaw({ t: "auth", password });
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
        // silently re-authenticate across reconnects if we have a password
        if (passwordRef.current) {
          setAuthBusy(true);
          ws.send(JSON.stringify({ t: "auth", password: passwordRef.current }));
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
            setAuthedBoth(true);
            setAuthBusy(false);
            setAuthError(null);
            setAirborneIp(msg.airborne ?? null);
            break;
          case "auth_fail":
            setAuthedBoth(false);
            setAuthBusy(false);
            setAuthError(msg.reason || "password rejected");
            passwordRef.current = null; // don't auto-retry a bad password
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

    // 50 Hz control uplink — only transmits once authenticated.
    const ctrlTimer = setInterval(() => {
      if (!openRef.current || !authedRef.current) return;
      const a = axesRef.current;
      wsRef.current.send(
        JSON.stringify({
          t: "ctrl",
          seq: seqRef.current++,
          ts: Date.now(),
          roll: +a.roll.toFixed(4),
          pitch: +a.pitch.toFixed(4),
          thr: +a.throttle.toFixed(4),
          yaw: +a.yaw.toFixed(4),
        })
      );
    }, CTRL_PERIOD_MS);

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
      clearInterval(ctrlTimer);
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
    login,
  };
}
