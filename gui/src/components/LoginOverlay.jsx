import { useRef, useState } from "react";

/**
 * Full-screen login gate shown until the operator's password authenticates to a
 * drone. The password is the drone token; the bridge probes its candidate IPs and
 * connects to the one that accepts it. Blocks the cockpit entirely until then.
 *
 * Props:
 *   wsReady  - is the WebSocket to the bridge open (can we submit yet)
 *   busy     - a login attempt is in flight
 *   error    - last failure reason, or null
 *   onSubmit - (password) => void
 */
export default function LoginOverlay({ wsReady, busy, error, onSubmit }) {
  const [password, setPassword] = useState("");
  const inputRef = useRef(null);

  const canSubmit = wsReady && !busy && password.length > 0;

  const submit = (e) => {
    e.preventDefault();
    if (canSubmit) onSubmit(password);
  };

  const status = !wsReady
    ? "connecting to ground bridge…"
    : busy
      ? "authenticating…"
      : error
        ? error
        : "enter the drone password to connect";

  return (
    <div id="login-overlay">
      <form id="login-card" onSubmit={submit}>
        <div className="login-badge">🛩️</div>
        <h1>Connect to Drone</h1>
        <p className={`login-status${error && !busy ? " err" : ""}`}>{status}</p>

        <input
          ref={inputRef}
          type="password"
          inputMode="text"
          autoFocus
          autoComplete="off"
          placeholder="Drone password"
          value={password}
          disabled={busy}
          onChange={(e) => setPassword(e.target.value)}
        />

        <button type="submit" disabled={!canSubmit}>
          {busy ? "CONNECTING…" : "CONNECT"}
        </button>

        <div className="login-hint">
          The right password connects to its drone. Wrong passwords are refused by
          the vehicle.
        </div>
      </form>
    </div>
  );
}
