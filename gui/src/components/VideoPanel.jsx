import { useEffect, useRef, useState } from "react";
import { VIDEO_URL, DETECT_ON_DEFAULT } from "../config.js";

const RECONNECT_MS = 3000;

// Stable-ish color per class label so the same object keeps its color frame to
// frame. Simple string hash -> hue; saturation/lightness fixed for readability.
function classColor(label) {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
  return `hsl(${h}, 85%, 60%)`;
}

/**
 * FPV video slot between the sticks. Shows the drone's MJPEG feed and overlays
 * object-detection boxes on a canvas layered over it.
 *
 * Inference happens on the ground Jetson's GPU (TensorRT) and arrives as `det`
 * messages on the telemetry WebSocket — the browser only draws. `det` is null
 * whenever the detector is absent or has gone quiet, which hides the overlay
 * rather than leaving stale boxes over live video.
 *
 * An <img> pointed at multipart/x-mixed-replace does not reliably fire `load` per
 * frame, so we treat the first `load` as "live" but keep the element visible the
 * whole time; `error` (the proxy's 502 when the camera is down) triggers a
 * backoff reconnect via a cache-busting nonce.
 */
export default function VideoPanel({ det }) {
  const imgRef = useRef(null);
  const canvasRef = useRef(null);
  const sizeRef = useRef({ w: 0, h: 0 });
  const [state, setState] = useState(VIDEO_URL ? "connecting" : "none");
  const [nonce, setNonce] = useState(0);
  const [detectOn, setDetectOn] = useState(DETECT_ON_DEFAULT);

  const live = state === "live";
  const showBoxes = detectOn && live && det != null;

  // ---- stream connection / reconnect ----
  useEffect(() => {
    if (!VIDEO_URL) return;
    const img = imgRef.current;
    let retry = 0;

    const onLoad = () => setState("live");
    const onError = () => {
      setState("error");
      retry = setTimeout(() => setNonce((n) => n + 1), RECONNECT_MS);
    };

    img.addEventListener("load", onLoad);
    img.addEventListener("error", onError);
    // cache-busting query forces a fresh stream connection on each (re)try
    const sep = VIDEO_URL.includes("?") ? "&" : "?";
    img.src = nonce ? `${VIDEO_URL}${sep}t=${nonce}` : VIDEO_URL;

    return () => {
      clearTimeout(retry);
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
    };
  }, [nonce]);

  // ---- draw detection boxes over the (letterboxed) video ----
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(rect.width * dpr);
    const h = Math.round(rect.height * dpr);
    // Assigning width/height reallocates and clears the backing store, so only do
    // it when the element actually changed size — boxes now arrive many times a
    // second and this effect runs on every one of them.
    if (sizeRef.current.w !== w || sizeRef.current.h !== h) {
      canvas.width = w;
      canvas.height = h;
      sizeRef.current = { w, h };
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);

    if (!showBoxes) return;
    const { boxes = [], srcW, srcH } = det;
    if (!srcW || !srcH || !boxes.length) return;

    // map source-frame pixels onto the object-fit:contain display rectangle
    const scale = Math.min(rect.width / srcW, rect.height / srcH);
    const offX = (rect.width - srcW * scale) / 2;
    const offY = (rect.height - srcH * scale) / 2;

    ctx.lineWidth = 2;
    ctx.font = "600 12px -apple-system, Segoe UI, Roboto, sans-serif";
    ctx.textBaseline = "top";
    for (const d of boxes) {
      const [bx, by, bw, bh] = d.bbox;
      const x = offX + bx * scale;
      const y = offY + by * scale;
      const w2 = bw * scale;
      const h2 = bh * scale;
      const color = classColor(d.class);
      ctx.strokeStyle = color;
      ctx.strokeRect(x, y, w2, h2);
      const label = `${d.class} ${Math.round(d.score * 100)}%`;
      const tw = ctx.measureText(label).width;
      const ty = y - 16 >= 0 ? y - 16 : y;
      ctx.fillStyle = color;
      ctx.fillRect(x - 1, ty, tw + 10, 16);
      ctx.fillStyle = "#04140a";
      ctx.fillText(label, x + 4, ty + 2);
    }
  }, [det, showBoxes]);

  const count = showBoxes ? (det.boxes?.length ?? 0) : 0;
  let badge = null;
  if (detectOn) {
    if (!live) badge = "waiting for video";
    else if (det == null) badge = "detector offline";
    else badge = `${count} object${count === 1 ? "" : "s"} · ${Math.round(det.fps ?? 0)}/s`;
  }

  const hint =
    state === "error"
      ? "signal lost — reconnecting…"
      : state === "connecting"
        ? "connecting to camera…"
        : "no source configured";

  return (
    <div id="videowrap">
      {VIDEO_URL && (
        <img
          id="video"
          ref={imgRef}
          alt=""
          style={{ display: live ? "block" : "none" }}
        />
      )}
      {/* detection overlay sits directly on top of the video, click-through */}
      <canvas id="detectcanvas" ref={canvasRef} />

      {VIDEO_URL && (
        <div id="detectbar">
          <button
            className={`detecttoggle ${detectOn ? "on" : ""}`}
            onClick={() => setDetectOn((v) => !v)}
            title="Toggle on-screen object detection"
          >
            <span className="dot" />
            DETECT {detectOn ? "ON" : "OFF"}
          </button>
          {badge && <span className="detectbadge">{badge}</span>}
        </div>
      )}

      {state !== "live" && (
        <div id="videoempty">
          <div className="big">VIDEO&nbsp;FEED</div>
          <div className="sm">{hint}</div>
        </div>
      )}
      <span className="brk tl" />
      <span className="brk tr" />
      <span className="brk bl" />
      <span className="brk br" />
    </div>
  );
}
