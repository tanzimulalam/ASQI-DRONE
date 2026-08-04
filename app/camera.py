"""Reverse proxy for the drone-side camera streamer.

Pipes the airborne camera_daemon's MJPEG feed through the ground bridge so the
browser only ever talks to its own origin — no drone IP baked into the GUI, and
it works unchanged whether the drone is on the hotspot or a wired bench link,
because the bridge already knows the airborne address.

Uses urllib from a sync path operation (run in Starlette's threadpool), so the
long-lived streaming read never blocks the event loop. httpx is intentionally not
required.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
from typing import Iterator

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse

from .settings import Settings, get_settings

log = logging.getLogger(__name__)

_CHUNK = 8192


def _camera_host(app: FastAPI, settings: Settings) -> str | None:
    """Explicit CAM host wins; otherwise follow the drone the operator logged into."""
    if settings.camera_host:
        return settings.camera_host
    session = getattr(app.state, "session", None)
    return session.remote_ip if session and session.active else None


def _open_upstream(path: str, host: str | None, settings: Settings):
    url = f"{settings.camera_base_url(host)}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "drone-ground-bridge"})
    return urllib.request.urlopen(req, timeout=settings.camera_connect_timeout_s)


def add_camera_routes(app: FastAPI) -> None:
    @app.get("/camera/stream.mjpg", include_in_schema=False)
    def camera_stream() -> Response:  # sync -> runs in threadpool
        settings = get_settings()
        host = _camera_host(app, settings)
        if host is None:
            return Response(status_code=502, content=b"not connected")
        try:
            upstream = _open_upstream("/stream.mjpg", host, settings)
        except (urllib.error.URLError, OSError) as exc:
            log.debug("camera upstream unavailable: %s", exc)
            return Response(status_code=502, content=b"camera offline")

        content_type = upstream.headers.get(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
        )

        def pump() -> Iterator[bytes]:
            try:
                while True:
                    chunk = upstream.read(_CHUNK)
                    if not chunk:
                        break
                    yield chunk
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                upstream.close()

        headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
        return StreamingResponse(pump(), media_type=content_type, headers=headers)

    @app.get("/camera/snapshot.jpg", include_in_schema=False)
    def camera_snapshot() -> Response:
        settings = get_settings()
        host = _camera_host(app, settings)
        if host is None:
            return Response(status_code=502, content=b"not connected")
        try:
            upstream = _open_upstream("/snapshot.jpg", host, settings)
            data = upstream.read()
            upstream.close()
        except (urllib.error.URLError, OSError) as exc:
            log.debug("camera snapshot unavailable: %s", exc)
            return Response(status_code=502, content=b"camera offline")
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/camera/healthz", include_in_schema=False)
    def camera_health() -> Response:
        settings = get_settings()
        host = _camera_host(app, settings)
        if host is None:
            return Response(
                status_code=502,
                content=b'{"connected":false,"error":"not connected"}',
                media_type="application/json",
            )
        try:
            upstream = _open_upstream("/healthz", host, settings)
            data = upstream.read()
            upstream.close()
        except (urllib.error.URLError, OSError):
            return Response(
                status_code=502,
                content=b'{"connected":false,"error":"camera daemon unreachable"}',
                media_type="application/json",
            )
        return Response(content=data, media_type="application/json")
