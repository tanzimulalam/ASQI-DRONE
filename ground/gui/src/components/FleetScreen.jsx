import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Pre-flight fleet screen: every aircraft this ground station knows, whether it
 * is reachable, its health, and a way to fly it.
 *
 * Two kinds of check, deliberately separate, mirroring app/fleet.py:
 *
 *   Reachability polls every few seconds. The bridge answers it with a TCP
 *   connect to each drone Jetson, which never touches the aircraft's control
 *   link, so it costs nothing to run continuously.
 *
 *   Health is a probe of the flight daemon itself. It runs once when an aircraft
 *   first shows up reachable, and again only when the operator presses "Check
 *   health". The bridge refuses to probe the aircraft being flown, because the
 *   daemon sends telemetry to whoever last contacted it; that aircraft's card
 *   shows its live session telemetry instead.
 *
 * Flying still requires typing that aircraft's password. The bridge holds tokens
 * only to read health, never to grant control, and never sends them here.
 *
 * Props:
 *   wsReady  - WebSocket to the bridge is open
 *   busy     - a login attempt is in flight
 *   error    - last login failure reason, or null
 *   onLogin  - (password, droneName) => void
 */

const POLL_MS = 4000;
// Both aircraft are configured with BATT_ARM_VOLT 14.0, read off the vehicles.
// Below that they will refuse to arm, which makes it a fact rather than a guess.
const BATT_ARM_V = 14.0;
const BATT_LOW_V = 14.8;

export default function FleetScreen({ wsReady, busy, error, onLogin }) {
  const [drones, setDrones] = useState(null);
  const [fetchErr, setFetchErr] = useState(null);
  const [checking, setChecking] = useState({});
  const [flying, setFlying] = useState(null); // name whose password field is open
  const [logoOk, setLogoOk] = useState(true);
  // Aircraft we have already auto-checked since they came online, so a card that
  // flaps online/offline is not probed on every poll.
  const autoChecked = useRef(new Set());

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/fleet", { cache: "no-store" });
      if (!r.ok) throw new Error(`bridge answered ${r.status}`);
      const data = await r.json();
      setDrones(data.drones);
      setFetchErr(null);
      return data.drones;
    } catch (e) {
      setFetchErr(e.message || "cannot reach the ground bridge");
      return null;
    }
  }, []);

  const checkHealth = useCallback(async (name) => {
    setChecking((c) => ({ ...c, [name]: true }));
    try {
      const r = await fetch(`/api/fleet/${encodeURIComponent(name)}/health`, {
        method: "POST",
      });
      const data = await r.json();
      if (r.ok) {
        setDrones((ds) => ds && ds.map((d) => (d.name === name ? data : d)));
      }
    } catch {
      /* the next poll will surface a dead bridge */
    } finally {
      setChecking((c) => ({ ...c, [name]: false }));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const ds = await refresh();
      if (!alive || !ds) return;
      for (const d of ds) {
        if (!d.reachable) {
          autoChecked.current.delete(d.name); // re-check when it comes back
          continue;
        }
        if (d.can_check_health && !d.active && !autoChecked.current.has(d.name)) {
          autoChecked.current.add(d.name);
          checkHealth(d.name);
        }
      }
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [refresh, checkHealth]);

  const online = drones ? drones.filter((d) => d.reachable).length : 0;

  let state, stateCls;
  if (fetchErr) {
    state = `ground bridge unreachable · ${fetchErr}`;
    stateCls = "r";
  } else if (!wsReady) {
    state = "linking to ground bridge";
    stateCls = "x";
  } else if (!drones) {
    state = "reading fleet";
    stateCls = "y";
  } else {
    state = `bridge online · ${online} of ${drones.length} aircraft reachable`;
    stateCls = online > 0 ? "g" : "y";
  }

  return (
    <div id="login-overlay">
      <div id="fleet-shell">
        <header className="fleet-head">
          <div className="login-brand">
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
          </div>
          <div className={`login-state ${stateCls}`}>
            <span className={`dot ${stateCls}`} />
            <span className="login-statetext">{state}</span>
          </div>
        </header>

        <div className="login-title">
          <span className="login-rule" />
          <span>Ground Control Station · Fleet</span>
          <span className="login-rule" />
        </div>

        <div className="fleet-grid">
          {drones === null && !fetchErr && <div className="fleet-empty">Reading fleet…</div>}
          {drones &&
            drones.map((d) => (
              <DroneCard
                key={d.name}
                drone={d}
                checking={!!checking[d.name]}
                onCheck={() => checkHealth(d.name)}
                open={flying === d.name}
                onOpen={() => setFlying(d.name)}
                onCancel={() => setFlying(null)}
                wsReady={wsReady}
                busy={busy && flying === d.name}
                error={flying === d.name ? error : null}
                onLogin={(pw) => onLogin(pw, d.name)}
              />
            ))}
        </div>

        <p className="login-hint">
          Reachability updates on its own. Health is read from each aircraft's flight
          daemon when it comes online and whenever you press Check health. Flying an
          aircraft always asks for its password; the aircraft itself decides whether
          to accept it.
        </p>
      </div>
    </div>
  );
}

/** Classify one card. Every visual state traces back to something the bridge knows. */
function cardState(d) {
  if (d.active) return { key: "flying", label: "IN FLIGHT SESSION", cls: "b" };
  if (!d.reachable) return { key: "offline", label: "OFFLINE", cls: "x" };
  if (!d.can_check_health) return { key: "nohealth", label: "ONLINE", cls: "g" };
  if (d.health) return { key: "healthy", label: "ONLINE", cls: "g" };
  if (d.health_age_s != null) return { key: "silent", label: "DAEMON SILENT", cls: "y" };
  return { key: "unchecked", label: "ONLINE", cls: "g" };
}

function DroneCard({
  drone: d,
  checking,
  onCheck,
  open,
  onOpen,
  onCancel,
  wsReady,
  busy,
  error,
  onLogin,
}) {
  const [pw, setPw] = useState("");
  const st = cardState(d);
  const h = d.health;

  const submit = (e) => {
    e.preventDefault();
    if (pw && wsReady && !busy) onLogin(pw);
  };

  return (
    <article className={`fleet-card s-${st.cls}`}>
      <div className="fleet-cardhead">
        <div className="fleet-name">{d.name}</div>
        {/* Prefixed modifier: the bare g/y/x classes are global dot fills and
            would paint the whole badge solid, hiding its text. */}
        <div className={`fleet-badge st-${st.cls}`}>
          <span className={`dot ${st.cls === "b" ? "g" : st.cls}`} />
          {st.label}
        </div>
      </div>
      <div className="fleet-ip">{d.ip}</div>

      <div className="fleet-body">
        {st.key === "offline" && (
          <p className="fleet-note">
            Jetson not reachable. Check the aircraft is powered and, for an aircraft
            on the drone hotspot, that the ground station's USB Wi-Fi adapter is in.
          </p>
        )}
        {st.key === "nohealth" && (
          <p className="fleet-note">
            Reachable. No token for this aircraft in the fleet file, so health can't
            be read before logging in.
          </p>
        )}
        {st.key === "unchecked" && <p className="fleet-note">Reading health…</p>}
        {st.key === "silent" && (
          <p className="fleet-note warn">
            The Jetson is up but its flight daemon did not answer. Check the
            drone-airborne service on the aircraft.
          </p>
        )}
        {h && <HealthGrid h={h} flying={st.key === "flying"} />}
      </div>

      {h && h.statustext && (
        <div className={`fleet-status ${/PreArm|fail|error/i.test(h.statustext) ? "warn" : ""}`}>
          {h.statustext}
        </div>
      )}

      {d.health_age_s != null && st.key !== "flying" && (
        <div className="fleet-age">checked {fmtAge(d.health_age_s)} ago</div>
      )}

      {open ? (
        <form className="fleet-login" onSubmit={submit}>
          <input
            type="password"
            autoFocus
            autoComplete="off"
            placeholder={`${d.name} password`}
            value={pw}
            disabled={busy}
            onChange={(e) => setPw(e.target.value)}
          />
          {error && <div className="fleet-err">{error}</div>}
          <div className="fleet-actions">
            <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
            <button type="submit" disabled={!pw || !wsReady || busy}>
              {busy ? "Connecting…" : `Fly ${d.name}`}
            </button>
          </div>
        </form>
      ) : (
        <div className="fleet-actions">
          <button
            type="button"
            className="ghost"
            onClick={onCheck}
            disabled={!d.reachable || !d.can_check_health || d.active || checking}
          >
            {checking ? "Checking…" : "Check health"}
          </button>
          <button type="button" onClick={onOpen} disabled={!d.reachable}>
            Fly {d.name}
          </button>
        </div>
      )}
    </article>
  );
}

// A reading this low is not a flat pack, it is no flight battery at all: an
// aircraft on the bench with its Jetson on wall power and the flight controller
// fed over USB. Showing "0.01 V" in red would read as an emergency.
const NO_BATTERY_V = 1.0;

function HealthGrid({ h, flying }) {
  const v = h.batt_v;
  const noBatt = v != null && v < NO_BATTERY_V;
  const battCls =
    v == null || noBatt ? "" : v < BATT_ARM_V ? "bad" : v < BATT_LOW_V ? "warn" : "ok";
  const battTxt = v == null ? "--" : noBatt ? "not connected" : `${v.toFixed(2)} V`;
  const fix = h.gps_fix ?? 0;
  const gpsCls = fix >= 3 ? "ok" : fix === 2 ? "warn" : "bad";
  const gpsTxt = `${h.sats ?? 0} sat · ${fix >= 3 ? "3D" : fix === 2 ? "2D" : "no fix"}`;

  return (
    <dl className="fleet-grid2">
      <Cell k="Battery" v={battTxt} cls={battCls} />
      <Cell k="GPS" v={gpsTxt} cls={gpsCls} />
      <Cell k="Mode" v={h.mode || "--"} />
      <Cell k="Armed" v={h.armed ? "ARMED" : "no"} cls={h.armed ? "warn" : ""} />
      <Cell k="EKF" v={h.ekf_ok ? "OK" : "check"} cls={h.ekf_ok ? "ok" : "warn"} />
      <Cell
        k="Failsafe"
        v={h.failsafe ? "ACTIVE" : "clear"}
        cls={h.failsafe ? "bad" : "ok"}
      />
      {flying && (
        <Cell
          k="Control age"
          v={h.ctrl_age_ms == null || h.ctrl_age_ms > 9999 ? "--" : `${h.ctrl_age_ms} ms`}
          cls={h.ctrl_age_ms > 350 ? "warn" : "ok"}
        />
      )}
    </dl>
  );
}

function Cell({ k, v, cls = "" }) {
  return (
    <div className="fleet-cell">
      <dt>{k}</dt>
      <dd className={cls}>{v}</dd>
    </div>
  );
}

function fmtAge(s) {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}
