import { useState } from "react";

/**
 * Corner brand mark.
 *
 * Drop the official university asset at `gui/public/brand/mtsu.svg` (or .png and
 * change LOGO_SRC) and it replaces the wordmark automatically — no code change.
 * Until then the CSS lockup below stands in, so the cockpit never ships with a
 * broken image or a guessed-at logo.
 */
const LOGO_SRC = "/brand/mtsu.svg";

export default function Brand() {
  const [haveLogo, setHaveLogo] = useState(true);

  return (
    <div className="brand" title="MTSU Intelligent & Secured Systems Laboratory">
      {haveLogo ? (
        <img
          className="brand-logo"
          src={LOGO_SRC}
          alt="MTSU"
          onError={() => setHaveLogo(false)}
        />
      ) : (
        <span className="brand-mark">MTSU</span>
      )}
      <span className="brand-rule" />
      <span className="brand-text">
        <span className="brand-lab">ISSL</span>
        <span className="brand-sub">Intelligent &amp; Secured Systems Lab</span>
      </span>
    </div>
  );
}
