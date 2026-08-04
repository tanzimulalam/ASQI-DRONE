import { useState } from "react";

/**
 * Corner brand lockup: MTSU mark, rule, lab name.
 *
 * The mark is the official MTSU athletics icon from the university's brand page
 * (goblueraiders.com/sports/2018/10/18/middle-tennessee-logos). Swap the file at
 * `gui/public/brand/mtsu.png` to change it — `mtsu-primary.png` (the full lockup
 * with Lightning) sits alongside it. If the file is ever missing the wordmark
 * below stands in, so the cockpit never renders a broken image.
 */
const LOGO_SRC = "/brand/mtsu.png";

export default function Brand() {
  const [haveLogo, setHaveLogo] = useState(true);

  return (
    <div className="brand" title="ASQI Lab — Autonomous Systems & Quantum Intelligence Laboratory">
      {haveLogo ? (
        <img
          className="brand-logo"
          src={LOGO_SRC}
          alt="Middle Tennessee State University"
          onError={() => setHaveLogo(false)}
        />
      ) : (
        <span className="brand-mark">MTSU</span>
      )}
      <span className="brand-rule" />
      <span className="brand-text">
        <span className="brand-lab">ASQI&nbsp;Lab</span>
        <span className="brand-sub">Autonomous Systems &amp; Quantum Intelligence</span>
      </span>
    </div>
  );
}
