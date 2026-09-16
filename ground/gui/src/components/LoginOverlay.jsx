import { useRef, useState } from "react";

/**
 * Full-screen login gate shown until the operator's password authenticates to a
 * drone. The password is the drone token; the bridge probes its candidate IPs and
 * connects to the one that accepts it. Blocks the cockpit entirely until then.
 *
 * The state line is deliberately explicit about which of the two links is being
 * talked about — browser-to-bridge, then bridge-to-aircraft. "Connection failed"
 * on a two-hop path tells the operator nothing about which hop to go look at.
 *
 * Props:
 *   wsReady  - is the WebSocket to the bridge open (can we submit yet)
 *   busy     - a login attempt is in flight
 *   error    - last failure reason, or null
 *   onSubmit - (password) => void
 */
export default function LoginOverlay({ wsReady, busy, error, onSubmit }) {
  const [password, setPassword] = useState("");
  const [logoOk, setLogoOk] = useState(true);
  const inputRef = useRef(null);

  const canSubmit = wsReady && !busy && password.length > 0;

  const submit = (e) => {
    e.preventDefault();
    if (canSubmit) onSubmit(password);
  };

  let state, stateCls;
  if (!wsReady) {
    state = "linking to ground bridge";
    stateCls = "x";
  } else if (busy) {
    state = "authenticating with aircraft";
    stateCls = "y";
  } else if (error) {
    state = error;
    stateCls = "r";
  } else {
    state = "bridge online · awaiting credentials";
    stateCls = "g";
  }

  return (
    <div id="login-overlay">
      <form id="login-card" onSubmit={submit}>
        <header className="login-brand">
          {logoOk ? (
            <img
              className="login-logo"
              src="/brand/mtsu.png"
              alt="Middle Tennessee State University"
              onError={() => setLogoOk(false)}
            />
          ) : (
            <span className="brand-mark">MTSU</span>
          )}
          <div className="login-brandtext">
            <span className="login-lab">ASQI&nbsp;Lab</span>
            <span className="login-labsub">
              Autonomous Systems &amp; Quantum Intelligence
            </span>
          </div>
        </header>

        <div className="login-title">
          <span className="login-rule" />
          <span>Ground Control Station</span>
          <span className="login-rule" />
        </div>

        <div className={`login-state ${stateCls}`}>
          <span className={`dot ${stateCls}`} />
          <span className="login-statetext">{state}</span>
        </div>

        <label className="login-field">
          <span className="login-fieldlabel">Drone password</span>
          <input
            ref={inputRef}
            type="password"
            inputMode="text"
            autoFocus
            autoComplete="off"
            placeholder="••••••••"
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        <button type="submit" disabled={!canSubmit}>
          {busy ? "Connecting…" : "Connect"}
        </button>

        <p className="login-hint">
          The password is the drone's session token. The bridge offers it to each
          known aircraft, and only the one that accepts it will answer — a wrong
          password is refused by the vehicle, not by this screen.
        </p>
      </form>
    </div>
  );
}
