import { useEffect, useRef } from "react";

const KNOB_HALF = 36; // knob is 72px; keep in sync with .knob in styles.css

/**
 * A self-centering gimbal pad. Both axes spring back to center on release, so
 * releasing the left stick's throttle returns to center = hold altitude
 * (ArduCopter AltHold/Loiter), like a DJI-style spring-centered gimbal.
 *
 * The knob is positioned imperatively through a ref and axis values are written
 * straight into `axesRef.current`, so dragging never triggers a React render.
 *
 * Props:
 *   axesRef   - shared mutable axes object from useDroneLink
 *   axisX     - key written from horizontal motion ("yaw" | "roll")
 *   axisY     - key written from vertical motion ("throttle" | "pitch")
 *   hint      - caption under the pad
 */
export default function Stick({ axesRef, axisX, axisY, hint }) {
  const padRef = useRef(null);
  const knobRef = useRef(null);

  useEffect(() => {
    const pad = padRef.current;
    const knob = knobRef.current;
    let active = null;
    let nx = 0;
    let ny = 0;

    const radius = () => pad.clientWidth / 2 - KNOB_HALF;

    const render = () => {
      const r = radius();
      knob.style.transform = `translate(${nx * r}px, ${ny * r}px)`;
    };

    const commit = () => {
      // screen y is down-positive; invert so pushing up = +1
      axesRef.current[axisX] = nx;
      axesRef.current[axisY] = -ny;
    };

    const setNorm = (x, y) => {
      nx = Math.max(-1, Math.min(1, x));
      ny = Math.max(-1, Math.min(1, y));
      render();
      commit();
    };

    const fromEvent = (ev) => {
      const rect = pad.getBoundingClientRect();
      const r = radius();
      setNorm(
        (ev.clientX - (rect.left + rect.width / 2)) / r,
        (ev.clientY - (rect.top + rect.height / 2)) / r
      );
    };

    const onDown = (e) => {
      active = e.pointerId;
      pad.setPointerCapture(e.pointerId);
      fromEvent(e);
    };
    const onMove = (e) => {
      if (active === e.pointerId) fromEvent(e);
    };
    const onUp = (e) => {
      if (active === e.pointerId) {
        active = null;
        setNorm(0, 0); // self-center both axes
      }
    };

    pad.addEventListener("pointerdown", onDown);
    pad.addEventListener("pointermove", onMove);
    pad.addEventListener("pointerup", onUp);
    pad.addEventListener("pointercancel", onUp);
    render();

    return () => {
      pad.removeEventListener("pointerdown", onDown);
      pad.removeEventListener("pointermove", onMove);
      pad.removeEventListener("pointerup", onUp);
      pad.removeEventListener("pointercancel", onUp);
      // release axes so a stale value can't linger after unmount
      axesRef.current[axisX] = 0;
      axesRef.current[axisY] = 0;
    };
  }, [axesRef, axisX, axisY]);

  return (
    <div className="side">
      <div className="pad" ref={padRef}>
        <div className="ring" />
        <div className="ch" />
        <div className="cv" />
        <div className="knob" ref={knobRef} />
      </div>
      <div className="stickhint">{hint}</div>
    </div>
  );
}
