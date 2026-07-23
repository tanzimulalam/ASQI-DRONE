"""Webcam auto-detection over V4L2.

Picks a working capture device by actually opening candidates and requiring that
frames flow, so metadata-only nodes (common on Jetson, e.g. odd /dev/videoN) and
busy/dead devices are skipped rather than guessed at.
"""
from __future__ import annotations

import glob
import logging
import re

import cv2

from .settings import CameraSettings

log = logging.getLogger(__name__)

_DEV_RE = re.compile(r"/dev/video(\d+)$")


def _device_index(dev: str) -> int | None:
    """Return the integer V4L2 index for '/dev/videoN' or a bare number, else None."""
    m = _DEV_RE.match(dev)
    if m:
        return int(m.group(1))
    if dev.isdigit():
        return int(dev)
    return None


def candidate_devices(settings: CameraSettings) -> list[str]:
    """Ordered list of devices to probe. Explicit CAM_DEVICE wins; else scan."""
    if settings.device and settings.device.lower() != "auto":
        return [settings.device]
    nodes = sorted(
        glob.glob("/dev/video*"),
        key=lambda p: (_device_index(p) if _device_index(p) is not None else 9999),
    )
    if nodes:
        return nodes
    # No device nodes visible (or no permission to glob) — fall back to indices.
    return [str(i) for i in range(10)]


def _configure(cap: "cv2.VideoCapture", settings: CameraSettings) -> None:
    # MJPG keeps USB bandwidth low and enables raw passthrough on most UVC cams.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
    cap.set(cv2.CAP_PROP_FPS, settings.fps)


def _yields_frames(cap: "cv2.VideoCapture", attempts: int = 15) -> bool:
    for _ in range(attempts):
        ok, frame = cap.read()
        if ok and frame is not None and getattr(frame, "size", 0) > 0:
            return True
    return False


def open_camera(settings: CameraSettings):
    """Open the first candidate that produces frames.

    Returns (cap, label) on success or (None, None) if nothing usable was found.
    The caller owns releasing ``cap``.
    """
    for dev in candidate_devices(settings):
        idx = _device_index(dev)
        try:
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2) if idx is not None else cv2.VideoCapture(dev)
        except Exception:  # pragma: no cover - defensive
            log.debug("open raised for %s", dev, exc_info=True)
            continue
        if not cap.isOpened():
            cap.release()
            continue
        _configure(cap, settings)
        if _yields_frames(cap):
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or settings.width
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or settings.height
            log.info("camera detected: %s (%dx%d)", dev, w, h)
            return cap, dev
        log.debug("device %s opened but produced no frames; skipping", dev)
        cap.release()
    return None, None
