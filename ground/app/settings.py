"""Ground-bridge configuration via environment variables (prefix ``GROUND_``)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_GUI_DIR = (Path(__file__).resolve().parent.parent / "gui").resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GROUND_", env_file=".env", extra="ignore")

    # Airborne daemon (drone Jetson) IP. Default is the drone hotspot's AP address
    # (NetworkManager shared mode); override via CLI/env for bench-over-wire.
    airborne_ip: str = "10.42.0.1"
    airborne_port: int = Field(default=14650, ge=1, le=65535)

    # Candidate drone IPs the login probe tries, in order. Comma-separated
    # (GROUND_AIRBORNE_IPS=10.42.0.1,192.168.1.50,...). Empty -> just airborne_ip.
    # The operator types only the password; the drone whose token matches connects.
    airborne_ips: str = ""
    # Per-candidate wait for a telemetry reply that proves the password was accepted.
    auth_probe_timeout_s: float = Field(default=0.8, gt=0, le=10)

    # HTTP/WebSocket server (single port serves GUI + /ws).
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)

    # Directory containing the web GUI (index.html + assets).
    gui_dir: Path = _GUI_DIR

    # Drone-side camera streamer (camera_daemon). The bridge reverse-proxies its
    # MJPEG feed at /camera/* so the browser stays same-origin and needs no drone
    # IP. Host defaults to the airborne daemon's IP; override only if the camera
    # runs elsewhere.
    camera_host: str = ""  # "" -> use airborne_ip
    camera_port: int = Field(default=8090, ge=1, le=65535)
    camera_connect_timeout_s: float = Field(default=3.0, gt=0, le=30)

    @property
    def candidate_ips(self) -> list[str]:
        listed = [ip.strip() for ip in self.airborne_ips.split(",") if ip.strip()]
        return listed or [self.airborne_ip]

    def camera_base_url(self, host: str | None = None) -> str:
        return f"http://{host or self.camera_host or self.airborne_ip}:{self.camera_port}"

    # Ground-side TensorRT detector (the `detector` package, run in the
    # jetson-inference container). The bridge long-polls it and forwards boxes to
    # browsers over the existing WebSocket. When it isn't running the bridge just
    # retries quietly and the cockpit shows no overlay — video is unaffected.
    detector_enabled: bool = True
    detector_url: str = "http://127.0.0.1:8091/detections"
    # Must exceed the detector's own long-poll window (10 s) or every poll times out.
    detector_timeout_s: float = Field(default=15.0, gt=0, le=60)
    detector_retry_s: float = Field(default=3.0, gt=0, le=60)

    # Tell the drone where we are this often (keeps telemetry flowing to us).
    keepalive_interval_s: float = Field(default=1.0, gt=0, le=10)

    # Telemetry older than this is considered a dead link (surfaced in /healthz).
    telemetry_stale_ms: int = Field(default=1500, ge=100)

    # Per-client outbound queue depth before we start dropping stale frames.
    ws_send_queue_max: int = Field(default=8, ge=1, le=256)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
