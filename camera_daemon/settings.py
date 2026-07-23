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
    # the camera/driver doesn't honor it.
    raw_passthrough: bool = field(default_factory=lambda: _env_bool("CAM_RAW_PASSTHROUGH", True))

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
        if errors:
            raise ValueError("invalid camera configuration:\n  - " + "\n  - ".join(errors))
