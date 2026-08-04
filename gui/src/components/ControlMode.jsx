import { KEY_MAX_DEFLECTION } from "../config.js";

/**
 * Control-source switch plus, in keyboard mode, a live map of the flight keys.
 *
 * Only one source drives the axes at a time. Both writing into `axesRef` would
 * mean whichever wrote last wins at 50 Hz, so the sticks are visibly disabled
 * while the keyboard has control (and vice versa).
 *
 * `held` is the Set of currently pressed key codes from useKeyboardControl; it
 * changes only on real press/release, so re-rendering on it is cheap.
 */

// Rendered key map. Arrow keys mirror the numpad, so each numpad cap shows both.
const PADS = [
  {
    label: "Throttle · Yaw",
    rows: [
      [null, { code: "KeyW", cap: "W", sub: "climb" }, null],
      [
        { code: "KeyA", cap: "A", sub: "yaw ←" },
        { code: "KeyS", cap: "S", sub: "descend" },
        { code: "KeyD", cap: "D", sub: "yaw →" },
      ],
    ],
  },
  {
    label: "Pitch · Roll",
    rows: [
      [null, { code: "Numpad8", alt: "ArrowUp", cap: "8", sub: "fwd" }, null],
      [
        { code: "Numpad4", alt: "ArrowLeft", cap: "4", sub: "left" },
        { code: "Numpad2", alt: "ArrowDown", cap: "2", sub: "back" },
        { code: "Numpad6", alt: "ArrowRight", cap: "6", sub: "right" },
      ],
    ],
  },
];

function Key({ spec, held }) {
  if (!spec) return <span className="keycap blank" />;
  const on = held.has(spec.code) || (spec.alt && held.has(spec.alt));
  return (
    <span className={`keycap${on ? " on" : ""}`}>
      <span className="cap">{spec.cap}</span>
      <span className="sub">{spec.sub}</span>
    </span>
  );
}

export default function ControlMode({ mode, onChange, held }) {
  const keyboard = mode === "keyboard";
  return (
    <div className="ctrlmode">
      <div className="modeswitch" role="group" aria-label="Control source">
        <button
          className={`modeopt${keyboard ? "" : " sel"}`}
          onClick={() => onChange("touch")}
          aria-pressed={!keyboard}
        >
          Sticks
        </button>
        <button
          className={`modeopt${keyboard ? " sel" : ""}`}
          onClick={() => onChange("keyboard")}
          aria-pressed={keyboard}
        >
          Keyboard
        </button>
      </div>

      {keyboard && (
        <div className="keymap">
          {PADS.map((pad) => (
            <div className="keypad" key={pad.label}>
              <div className="keypad-label">{pad.label}</div>
              {pad.rows.map((row, i) => (
                <div className="keyrow" key={i}>
                  {row.map((spec, j) => (
                    <Key spec={spec} held={held} key={spec ? spec.code : `b${j}`} />
                  ))}
                </div>
              ))}
            </div>
          ))}
          <div className="keynote">
            Arrow keys mirror the numpad. Throttle self-centers, so releasing holds
            altitude. Keys are limited to {Math.round(KEY_MAX_DEFLECTION * 100)}% stick
            and eased in.
          </div>
        </div>
      )}
    </div>
  );
}
