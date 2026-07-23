"""Capture thread: owns the camera, publishes the newest JPEG frame.

One background thread detects/opens the webcam, reads frames, and publishes the
latest JPEG behind a condition variable. HTTP client threads wait on that variable
and are handed the freshest frame, so a slow client never backs up capture and the
capture rate is paced by the camera, not by a busy loop. If the camera disappears,
the thread drops it and re-detects, so unplug/replug recovers on its own.
"""
from __future__ import annotations

import logging
import threading
import time

import cv2

from .detect import open_camera
from .settings import CameraSettings

log = logging.getLogger(__name__)


class CameraStream:
    def __init__(self, settings: CameraSettings) -> None:
        self._s = settings
        self._cond = threading.Condition()
        self._frame: bytes | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # diagnostics (guarded by _cond)
        self._device: str | None = None
        self._connected = False
        self._last_error: str | None = None
        self._fps = 0.0

    # ---- lifecycle ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="camera-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=3.0)

    # ---- producer ----
    def _run(self) -> None:
        while not self._stop.is_set():
            cap, dev = open_camera(self._s)
            if cap is None:
                self._set_status(connected=False, device=None,
                                 error="no camera detected")
                self._stop.wait(self._s.retry_interval_s)
                continue
            self._set_status(connected=True, device=dev, error=None)
            try:
                self._pump(cap)
            finally:
                cap.release()
                self._set_status(connected=False, device=dev, error="camera disconnected")

    def _pump(self, cap: "cv2.VideoCapture") -> None:
        # Try zero-copy JPEG passthrough; verify on the first frames and fall back
        # to decode+encode if the driver doesn't deliver JPEG buffers.
        passthrough = False
        if self._s.raw_passthrough:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0.0)
            passthrough = self._verify_passthrough(cap)
            if not passthrough:
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 1.0)
                log.info("MJPG passthrough unavailable; using decode+encode")

        fails = 0
        frames = 0
        t0 = time.monotonic()
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                fails += 1
                if fails >= self._s.read_fail_limit:
                    log.warning("camera read failed %d times; re-detecting", fails)
                    return
                continue
            fails = 0
            jpg = self._encode(frame, passthrough)
            if jpg is None:
                continue
            self._publish(jpg)

            frames += 1
            dt = time.monotonic() - t0
            if dt >= 1.0:
                with self._cond:
                    self._fps = frames / dt
                frames = 0
                t0 = time.monotonic()

    def _verify_passthrough(self, cap: "cv2.VideoCapture", attempts: int = 10) -> bool:
        for _ in range(attempts):
            ok, frame = cap.read()
            if ok and frame is not None and frame.ndim == 1:
                b = frame.tobytes()
                if b[:2] == b"\xff\xd8":  # JPEG SOI
                    return True
        return False

    def _encode(self, frame, passthrough: bool) -> bytes | None:
        # Raw 1-D buffer: either a JPEG (passthrough) or unusable — never imencode it.
        if getattr(frame, "ndim", 0) == 1:
            b = frame.tobytes()
            return b if b[:2] == b"\xff\xd8" else None
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._s.jpeg_quality]
        )
        return buf.tobytes() if ok else None

    def _publish(self, jpg: bytes) -> None:
        with self._cond:
            self._frame = jpg
            self._seq += 1
            self._cond.notify_all()

    def _set_status(self, *, connected: bool, device: str | None, error: str | None) -> None:
        with self._cond:
            self._connected = connected
            self._device = device
            self._last_error = error
            if not connected:
                self._fps = 0.0

    # ---- consumer API (HTTP handlers) ----
    def next_frame(self, last_seq: int, timeout: float) -> tuple[int, bytes | None]:
        """Block up to ``timeout`` for a frame newer than ``last_seq``.

        Returns (seq, jpeg). seq == last_seq with a frame means "no new frame yet";
        callers should just loop. jpeg is None only before the first frame ever.
        """
        with self._cond:
            if self._seq == last_seq:
                self._cond.wait(timeout)
            return self._seq, self._frame

    def snapshot(self) -> bytes | None:
        with self._cond:
            return self._frame

    def status(self) -> dict:
        with self._cond:
            return {
                "connected": self._connected,
                "device": self._device,
                "fps": round(self._fps, 1),
                "have_frame": self._frame is not None,
                "error": self._last_error,
            }
