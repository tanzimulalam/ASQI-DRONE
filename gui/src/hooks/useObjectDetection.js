import { useEffect, useRef, useState } from "react";
import {
  DETECT_MODEL_URL,
  DETECT_INTERVAL_MS,
  DETECT_MIN_SCORE,
  DETECT_MAX_BOXES,
} from "../config.js";

// Load the model once for the whole app, no matter how many times the panel
// mounts. Kept at module scope so toggling detection off/on never re-downloads
// the ~18 MB of weights. Resolves to the coco-ssd model or rejects on failure.
// TensorFlow.js (~1.9 MB) is dynamically imported so it is code-split out of the
// main bundle: the cockpit renders immediately and this chunk loads only when
// detection first activates.
let _modelPromise = null;
function loadModel() {
  if (!_modelPromise) {
    _modelPromise = (async () => {
      const tf = await import("@tensorflow/tfjs");
      const cocoSsd = await import("@tensorflow-models/coco-ssd");
      // WebGL is the fast path in a browser; fall back to CPU if it's missing so
      // detection still runs (slowly) rather than erroring out entirely.
      try {
        await tf.setBackend("webgl");
      } catch {
        await tf.setBackend("cpu");
      }
      await tf.ready();
      const cfg = { base: "lite_mobilenet_v2" };
      if (DETECT_MODEL_URL) cfg.modelUrl = DETECT_MODEL_URL;
      return cocoSsd.load(cfg);
    })().catch((err) => {
      _modelPromise = null; // allow a later retry
      throw err;
    });
  }
  return _modelPromise;
}

/**
 * Runs COCO-SSD object detection on a live <img> (the MJPEG feed) on a throttled
 * loop and returns the latest detections plus a status string. Inference is
 * self-paced (never overlaps) and only runs while `active` is true, so turning
 * the overlay off costs nothing.
 *
 * Returns detections as `{ bbox:[x,y,w,h], class, score }` in the *source frame*
 * pixel space, alongside the `srcW/srcH` they were measured against so the caller
 * can map them onto a letterboxed (object-fit:contain) video element.
 */
export function useObjectDetection(imgRef, active) {
  const [status, setStatus] = useState("idle"); // idle | loading | ready | error
  const [result, setResult] = useState({ boxes: [], srcW: 0, srcH: 0 });
  const [fps, setFps] = useState(0);
  // keep the freshest boxes in a ref so a paused loop can resume without stale UI
  const stopRef = useRef(false);

  useEffect(() => {
    if (!active) {
      setStatus("idle");
      setResult({ boxes: [], srcW: 0, srcH: 0 });
      return;
    }
    stopRef.current = false;
    let model = null;
    let timer = 0;
    let frames = 0;
    let fpsT0 = performance.now();

    setStatus("loading");
    loadModel().then(
      (m) => {
        if (stopRef.current) return;
        model = m;
        setStatus("ready");
        loop();
      },
      (err) => {
        if (stopRef.current) return;
        console.error("object-detection model load failed:", err);
        setStatus("error");
      },
    );

    async function loop() {
      if (stopRef.current) return;
      const cycleStart = performance.now();
      const img = imgRef.current;
      const ready = img && img.naturalWidth > 0 && img.complete;
      if (model && ready) {
        try {
          const preds = await model.detect(img, DETECT_MAX_BOXES, DETECT_MIN_SCORE);
          if (stopRef.current) return;
          setResult({
            boxes: preds,
            srcW: img.naturalWidth,
            srcH: img.naturalHeight,
          });
          frames += 1;
          const dt = performance.now() - fpsT0;
          if (dt >= 1000) {
            setFps(Math.round((frames / dt) * 1000));
            frames = 0;
            fpsT0 = performance.now();
          }
        } catch (err) {
          console.debug("detect() skipped a frame:", err);
        }
      }
      // self-pace: schedule the next run only after this one finishes (never
      // overlaps), waiting out the remainder of the target interval.
      const spent = performance.now() - cycleStart;
      timer = setTimeout(loop, Math.max(0, DETECT_INTERVAL_MS - spent));
    }

    return () => {
      stopRef.current = true;
      clearTimeout(timer);
    };
  }, [active, imgRef]);

  return { status, result, fps };
}
