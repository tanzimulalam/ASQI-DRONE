/**
 * Why the aircraft will not arm, said out loud in the cockpit.
 *
 * The flight controller already reports its pre-arm refusals in telemetry, but
 * until now they were only visible in the drone's log over SSH. Standing in
 * front of an aircraft that refuses to arm, with no idea why, is the worst
 * moment to go looking for a terminal.
 *
 * Shown only while disarmed: once armed, these messages are stale by definition.
 */
const HINTS = [
  [/safety switch/i, "Press and hold the hardware safety switch on the aircraft until its light is solid."],
  [/position|gps|fence|home/i, "Indoors there is no GPS. Use ALT HOLD, and check the aircraft is in indoor configuration."],
  [/compass/i, "Compass problem. Check the GPS mast cable is plugged in."],
  [/rc|radio|throttle/i, "Turn the transmitter on with the throttle stick fully down."],
  [/battery/i, "Battery is below the arming voltage. Fit a charged pack."],
  [/gyro|accel|ins/i, "Sensors disagree. Set the aircraft down, keep it still, and power-cycle it."],
  [/rangefinder/i, "A rangefinder is configured but not detected. It can be turned off in the parameters."],
];

export default function PreArmBanner({ tlm }) {
  const text = tlm?.statustext || "";
  const armed = !!tlm?.armed;
  if (armed || !/prearm/i.test(text)) return null;

  const reason = text.replace(/^\s*PreArm:\s*/i, "");
  const hint = HINTS.find(([re]) => re.test(reason));

  return (
    <div className="prearm-banner">
      <span className="prearm-tag">CANNOT ARM</span>
      <span className="prearm-reason">{reason}</span>
      {hint && <span className="prearm-hint">{hint[1]}</span>}
    </div>
  );
}
