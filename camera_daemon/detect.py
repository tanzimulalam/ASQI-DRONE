"""Webcam auto-detection over GStreamer and V4L2.

Picks a working capture device by actually opening candidates and requiring that
frames flow, so metadata-only nodes (common on Jetson, e.g. odd /dev/videoN) and
busy/dead devices are skipped rather than guessed at.

Each candidate is tried on the GStreamer backend first (lowest latency — see
``gst_pipeline``) and then on plain V4L2, so a camera that won't negotiate MJPG
through GStreamer still comes up on the original path.
"""
from __future__ import annotations

import glob
import logging
import re
from typing import Callable, Iterator, NamedTuple

import cv2

from .settings import CameraSettings

log = logging.getLogger(__name__)

_DEV_RE = re.compile(r"/dev/video(\d+)$")
# Guards interpolation into the pipeline string: GStreamer parses that string, so a
# device name carrying '!' or spaces could otherwise graft on pipeline elements.
_DEV_PATH_RE = re.compile(r"^/dev/video\d+$")


class OpenedCamera(NamedTuple):
    """An open capture plus how it was opened. All-None when nothing was found."""

    cap: "cv2.VideoCapture | None"
    label: str | None
    backend: str | None


NO_CAMERA = OpenedCamera(None, None, None)


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


def gst_pipeline(dev: str, settings: CameraSettings) -> str | None:
    """JPEG-passthrough pipeline for ``dev``, or None if one can't be built.

    ``max-buffers=1 drop=true`` is the whole point: the sink holds only the newest
    frame, so whenever the consumer falls behind the backlog is discarded at the
    source rather than aging in V4L2's buffer ring. ``sync=false`` hands frames over
    as they arrive instead of pacing them against a clock.
    """
    if settings.gst_pipeline:
        return settings.gst_pipeline
    idx = _device_index(dev)
    if idx is None:
        return None
    path = f"/dev/video{idx}"
    if not _DEV_PATH_RE.match(path):  # pragma: no cover - _device_index guarantees it
        return None
    return (
        f"v4l2src device={path} io-mode=2 "
        f"! image/jpeg,width={settings.width},height={settings.height},"
        f"framerate={settings.fps}/1 "
        f"! appsink max-buffers=1 drop=true sync=false"
    )


def _open_gstreamer(dev: str, settings: CameraSettings) -> "cv2.VideoCapture | None":
    pipeline = gst_pipeline(dev, settings)
    if pipeline is None:
        return None
    try:
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    except Exception:  # pragma: no cover - defensive
        log.debug("gstreamer open raised for %s", dev, exc_info=True)
        return None
    if not cap.isOpened():
        # Normal when the camera has no MJPG mode at this size/rate: caps
        # negotiation fails and we fall through to V4L2.
        cap.release()
        return None
    return cap


def _open_v4l2(dev: str, settings: CameraSettings) -> "cv2.VideoCapture | None":
    idx = _device_index(dev)
    try:
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2) if idx is not None else cv2.VideoCapture(dev)
    except Exception:  # pragma: no cover - defensive
        log.debug("v4l2 open raised for %s", dev, exc_info=True)
        return None
    if not cap.isOpened():
        cap.release()
        return None
    # MJPG keeps USB bandwidth low and enables raw passthrough on most UVC cams.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
    cap.set(cv2.CAP_PROP_FPS, settings.fps)
    return cap


_Opener = Callable[[str, CameraSettings], "cv2.VideoCapture | None"]


def _openers(settings: CameraSettings) -> Iterator[tuple[str, _Opener]]:
    """Backends to try per device, best-latency first."""
    if settings.try_gstreamer:
        yield "gstreamer", _open_gstreamer
    if settings.try_v4l2:
        yield "v4l2", _open_v4l2


def _yields_frames(cap: "cv2.VideoCapture", attempts: int = 15) -> bool:
    for _ in range(attempts):
        ok, frame = cap.read()
        if ok and frame is not None and getattr(frame, "size", 0) > 0:
            return True
    return False


def open_camera(settings: CameraSettings) -> OpenedCamera:
    """Open the first candidate that produces frames.

    Returns the open capture with its device label and backend name, or
    ``NO_CAMERA`` if nothing usable was found. The caller owns releasing ``cap``.
    """
    for dev in candidate_devices(settings):
        for backend, opener in _openers(settings):
            cap = opener(dev, settings)
            if cap is None:
                continue
            if _yields_frames(cap):
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or settings.width
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or settings.height
                log.info("camera detected: %s via %s (%dx%d)", dev, backend, w, h)
                return OpenedCamera(cap, dev, backend)
            log.debug("device %s opened on %s but produced no frames", dev, backend)
            cap.release()
    return NO_CAMERA
