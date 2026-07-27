"""Capture thread: owns the camera, publishes the newest JPEG frame.

One background thread detects/opens the webcam, reads frames, and publishes the
latest JPEG behind a condition variable. HTTP client threads wait on that variable
and are handed the freshest frame, so a slow client never backs up capture and the
capture rate is paced by the camera, not by a busy loop. If the camera disappears,
the thread drops it and re-detects, so unplug/replug recovers on its own.

Whichever backend ``open_camera`` picked, the goal is the same: get the camera's
own JPEG out to clients without decoding it. GStreamer negotiates image/jpeg in the
pipeline; V4L2 gets there by switching CONVERT_RGB off. Either way the frames come
back as encoded byte buffers, and only a camera that refuses both costs us a
decode+encode round trip.
"""
from __future__ import annotations

import logging
import threading
import time

import cv2

from .detect import open_camera
from .settings import CameraSettings

log = logging.getLogger(__name__)

_SOI = b"\xff\xd8"  # JPEG start-of-image marker


def _is_encoded_buffer(frame) -> bool:
    """True if ``frame`` is a byte buffer of an already-encoded frame, not an image.

    The two passthrough paths shape it differently: V4L2 with CONVERT_RGB off yields
    a 1-D buffer, while OpenCV's GStreamer backend hands the same bytes back as a
    single (1, N) row. Telling them apart from a real image matters because an
    encoded buffer must never reach ``imencode`` — that call would succeed and emit
    a one-pixel-tall JPEG of the byte string rather than failing loudly.
    """
    ndim = getattr(frame, "ndim", 0)
    if ndim == 1:
        return True
    return ndim == 2 and getattr(frame, "shape", (0,))[0] == 1


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
        self._backend: str | None = None
        self._passthrough = False
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
            cap, dev, backend = open_camera(self._s)
            if cap is None:
                self._set_status(connected=False, device=None, backend=None,
                                 error="no camera detected")
                self._stop.wait(self._s.retry_interval_s)
                continue
            self._set_status(connected=True, device=dev, backend=backend, error=None)
            try:
                self._pump(cap, backend)
            finally:
                cap.release()
                self._set_status(connected=False, device=dev, backend=backend,
                                 error="camera disconnected")

    def _pump(self, cap: "cv2.VideoCapture", backend: str | None) -> None:
        # Establish JPEG passthrough, then verify it on the first frames and fall
        # back to decode+encode if the frames aren't actually JPEG.
        if backend == "gstreamer":
            # The pipeline already negotiated image/jpeg, so there is no CONVERT_RGB
            # to turn off — just confirm the sink is really handing over JPEG.
            passthrough = self._verify_passthrough(cap)
            if not passthrough:
                log.info("gstreamer sink is not delivering JPEG; using decode+encode")
        elif self._s.raw_passthrough:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0.0)
            passthrough = self._verify_passthrough(cap)
            if not passthrough:
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 1.0)
                log.info("MJPG passthrough unavailable; using decode+encode")
        else:
            passthrough = False
        # Surfaced in /healthz: it's the difference between forwarding the camera's
        # own JPEG and spending CPU re-encoding every frame.
        with self._cond:
            self._passthrough = passthrough

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
            jpg = self._encode(frame)
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
            if ok and frame is not None and _is_encoded_buffer(frame):
                if frame.tobytes()[:2] == _SOI:
                    return True
        return False

    def _encode(self, frame) -> bytes | None:
        # Encoded buffer: either a JPEG (passthrough) or unusable — never imencode it.
        if _is_encoded_buffer(frame):
            b = frame.tobytes()
            return b if b[:2] == _SOI else None
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._s.jpeg_quality]
        )
        return buf.tobytes() if ok else None

    def _publish(self, jpg: bytes) -> None:
        with self._cond:
            self._frame = jpg
            self._seq += 1
            self._cond.notify_all()

    def _set_status(self, *, connected: bool, device: str | None,
                    backend: str | None, error: str | None) -> None:
        with self._cond:
            self._connected = connected
            self._device = device
            self._backend = backend
            self._last_error = error
            if not connected:
                self._fps = 0.0
                self._passthrough = False

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
                "backend": self._backend,
                "passthrough": self._passthrough,
                "fps": round(self._fps, 1),
                "have_frame": self._frame is not None,
                "error": self._last_error,
            }
