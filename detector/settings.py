"""Detector configuration (env prefix ``DET_``), validated at startup.

Mirrors the camera daemon's settings style so both services are configured the
same way: environment variables only, checked once at boot, no source edits.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

_NETWORKS = frozenset({
    "ssd-mobilenet-v2", "ssd-mobilenet-v1", "ssd-inception-v2",
    "pednet", "multiped", "facenet", "coco-dog", "coco-bottle", "coco-chair",
})


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


@dataclass(frozen=True, slots=True)
class DetectorSettings:
    # --- Upstream video ---
    # The drone's camera daemon. Pulled directly rather than through the ground
    # bridge's proxy so detection keeps running even while no browser is connected,
    # and so a stalled browser can never stall inference.
    stream_url: str = field(
        default_factory=lambda: _env_str("DET_STREAM_URL", "http://10.42.0.1:8090/stream.mjpg")
    )
    connect_timeout_s: float = field(default_factory=lambda: _env_float("DET_CONNECT_TIMEOUT_S", 5.0))
    # How long to wait before redialling a stream that dropped or went silent.
    retry_interval_s: float = field(default_factory=lambda: _env_float("DET_RETRY_S", 2.0))
    # No frame for this long means the upstream is wedged; drop it and redial.
    read_timeout_s: float = field(default_factory=lambda: _env_float("DET_READ_TIMEOUT_S", 5.0))

    # --- HTTP server (the ground bridge polls this) ---
    host: str = field(default_factory=lambda: _env_str("DET_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("DET_PORT", 8091))

    # --- Inference ---
    # detectNet model name; jetson-inference downloads it on first use.
    network: str = field(default_factory=lambda: _env_str("DET_NETWORK", "ssd-mobilenet-v2"))
    threshold: float = field(default_factory=lambda: _env_float("DET_THRESHOLD", 0.5))
    # Cap boxes per frame so the overlay stays readable and the JSON stays small.
    max_boxes: int = field(default_factory=lambda: _env_int("DET_MAX_BOXES", 20))

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not self.stream_url.startswith(("http://", "https://")):
            errors.append(f"stream_url {self.stream_url!r} must be an http(s) URL")
        if not (1 <= self.port <= 65535):
            errors.append(f"port {self.port} out of range")
        if not (0.0 < self.threshold <= 1.0):
            errors.append(f"threshold {self.threshold} out of (0, 1]")
        if self.max_boxes < 1:
            errors.append("max_boxes must be >= 1")
        if self.connect_timeout_s <= 0:
            errors.append("connect_timeout_s must be > 0")
        if self.retry_interval_s <= 0:
            errors.append("retry_interval_s must be > 0")
        if self.read_timeout_s <= 0:
            errors.append("read_timeout_s must be > 0")
        # Not fatal: jetson-inference accepts paths to custom ONNX models too, so an
        # unknown name may still be valid. Warn-by-convention rather than reject.
        if errors:
            raise ValueError("invalid detector configuration:\n  - " + "\n  - ".join(errors))

    @property
    def known_network(self) -> bool:
        return self.network in _NETWORKS
