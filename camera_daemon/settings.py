"""Camera-streamer configuration (env prefix ``CAM_``), validated at startup."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key}={raw!r} is not an integer") from exc


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key}={raw!r} is not a number") from exc


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_BACKENDS = frozenset({"auto", "gstreamer", "v4l2"})


@dataclass(frozen=True, slots=True)
class CameraSettings:
    # --- HTTP server ---
    host: str = field(default_factory=lambda: _env_str("CAM_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("CAM_PORT", 8090))

    # --- Capture device ---
    # "auto" scans /dev/video* (then indices 0..9) and picks the first that yields
    # frames. Or force one: CAM_DEVICE=/dev/video1 or CAM_DEVICE=0.
    device: str = field(default_factory=lambda: _env_str("CAM_DEVICE", "auto"))
    width: int = field(default_factory=lambda: _env_int("CAM_WIDTH", 640))
    height: int = field(default_factory=lambda: _env_int("CAM_HEIGHT", 480))
    fps: int = field(default_factory=lambda: _env_int("CAM_FPS", 30))
    jpeg_quality: int = field(default_factory=lambda: _env_int("CAM_JPEG_QUALITY", 80))

    # Ask the webcam for MJPG and forward its JPEG frames without decoding +
    # re-encoding (much lower CPU). Verified at open; falls back to decode mode if
    # the camera/driver doesn't honor it. Applies to the v4l2 backend; the gstreamer
    # backend negotiates image/jpeg in the pipeline itself.
    raw_passthrough: bool = field(default_factory=lambda: _env_bool("CAM_RAW_PASSTHROUGH", True))

    # --- Capture backend ---
    # "gstreamer" pulls the camera's JPEG through
    #     v4l2src ! image/jpeg ! appsink max-buffers=1 drop=true
    # so the sink keeps only the newest frame and stale ones are dropped at the
    # source instead of queueing in V4L2's buffer ring — the lowest-latency path,
    # and still zero decode/encode. "v4l2" is the plain OpenCV path. "auto" tries
    # gstreamer per device and falls back to v4l2 when it won't open or won't
    # deliver frames (e.g. a camera with no MJPG mode at this resolution).
    backend: str = field(
        default_factory=lambda: _env_str("CAM_BACKEND", "auto").strip().lower()
    )

    # Full GStreamer pipeline override, used verbatim in place of the generated one
    # (so pair it with an explicit CAM_DEVICE). Must end in an appsink delivering
    # image/jpeg or raw video. Escape hatch for odd cameras and for transcoding
    # pipelines, e.g. a camera with no MJPG mode re-encoded on the Jetson's JPEG
    # engine: "v4l2src device=/dev/video0 ! video/x-raw,format=YUY2 ! nvvidconv
    #          ! nvjpegenc ! image/jpeg ! appsink max-buffers=1 drop=true sync=false"
    gst_pipeline: str = field(default_factory=lambda: _env_str("CAM_GST_PIPELINE", ""))

    # --- Robustness ---
    # How often to rescan for a camera when none is present (hot-plug support).
    retry_interval_s: float = field(default_factory=lambda: _env_float("CAM_RETRY_S", 2.0))
    # Consecutive failed reads before we drop the device and re-detect.
    read_fail_limit: int = field(default_factory=lambda: _env_int("CAM_READ_FAIL_LIMIT", 30))

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not (1 <= self.port <= 65535):
            errors.append(f"port {self.port} out of range")
        if not (16 <= self.width <= 4096):
            errors.append(f"width {self.width} out of [16, 4096]")
        if not (16 <= self.height <= 4096):
            errors.append(f"height {self.height} out of [16, 4096]")
        if not (1 <= self.fps <= 120):
            errors.append(f"fps {self.fps} out of [1, 120]")
        if not (1 <= self.jpeg_quality <= 100):
            errors.append(f"jpeg_quality {self.jpeg_quality} out of [1, 100]")
        if self.retry_interval_s <= 0:
            errors.append("retry_interval_s must be > 0")
        if self.read_fail_limit < 1:
            errors.append("read_fail_limit must be >= 1")
        if self.backend not in _BACKENDS:
            errors.append(f"backend {self.backend!r} not one of {sorted(_BACKENDS)}")
        if self.gst_pipeline and "appsink" not in self.gst_pipeline:
            errors.append("gst_pipeline must end in an appsink")
        if errors:
            raise ValueError("invalid camera configuration:\n  - " + "\n  - ".join(errors))

    @property
    def try_gstreamer(self) -> bool:
        return self.backend in ("auto", "gstreamer")

    @property
    def try_v4l2(self) -> bool:
        return self.backend in ("auto", "v4l2")
