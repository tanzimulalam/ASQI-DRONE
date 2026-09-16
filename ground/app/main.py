"""FastAPI application: static GUI, WebSocket relay, and health endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError

from . import PROTOCOL_VERSION, __version__
from .camera import add_camera_routes
from .detections import detection_relay
from .hub import Hub
from .protocol import AuthIn, ClientMessage
from .session import Session
from .settings import Settings, get_settings
from .udp_link import UdpLink

log = logging.getLogger(__name__)

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)-5s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    hub = Hub(queue_max=settings.ws_send_queue_max)
    session = Session()

    def on_telemetry(msg: dict[str, Any]) -> None:
        hub.broadcast(json.dumps(msg, separators=(",", ":")))

    udp = UdpLink(settings.airborne_port, on_telemetry, bind_host=settings.host)
    await udp.start()

    keepalive = asyncio.create_task(_keepalive(udp, session, settings), name="keepalive")

    app.state.settings = settings
    app.state.hub = hub
    app.state.udp = udp
    app.state.session = session

    detections = (
        asyncio.create_task(detection_relay(app, settings), name="detections")
        if settings.detector_enabled
        else None
    )
    log.info(
        "ground bridge ready (v%s, proto %d); login-gated, candidates: %s",
        __version__, PROTOCOL_VERSION, ", ".join(settings.candidate_ips),
    )
    try:
        yield
    finally:
        for task in (keepalive, detections):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        udp.stop()
        log.info("ground bridge stopped")


async def _keepalive(udp: UdpLink, session: Session, settings: Settings) -> None:
    """Once a session is active, tell the drone our address so telemetry flows.

    Before anyone logs in there is no token and no drone address, so nothing is
    sent — the drone stays disconnected until the right password authenticates.
    """
    while True:
        if session.active:
            udp.send({"t": "hb", "token": session.token}, session.remote)
        await asyncio.sleep(settings.keepalive_interval_s)


def create_app() -> FastAPI:
    app = FastAPI(title="Drone ground bridge", version=__version__, lifespan=lifespan)

    gui_dir = get_settings().gui_dir
    dist_dir = gui_dir / "dist"
    spa_built = (dist_dir / "index.html").is_file()

    if not spa_built:
        # No React build yet: serve the checked-in vanilla GUI so the bridge still
        # works out of the box. Run `npm --prefix gui install && npm --prefix gui
        # run build` to produce dist/, then this branch is skipped on next start.
        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            legacy = gui_dir / "legacy" / "index.html"
            return FileResponse(legacy if legacy.is_file() else gui_dir / "index.html")

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        udp: UdpLink = app.state.udp
        settings: Settings = app.state.settings
        hub: Hub = app.state.hub
        session: Session = app.state.session
        age = udp.telemetry_age_ms
        link_ok = session.active and age is not None and age <= settings.telemetry_stale_ms
        active = f"{session.remote_ip}:{settings.airborne_port}" if session.active else None
        return JSONResponse(
            {
                "status": "ok" if link_ok else "degraded",
                "authenticated": session.active,
                "airborne": active,
                "candidates": settings.candidate_ips,
                "telemetry_age_ms": age,
                "telemetry_link_ok": link_ok,
                "clients": hub.client_count,
                "proto": PROTOCOL_VERSION,
            }
        )

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        return JSONResponse({"ready": True})

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await _handle_ws(app, websocket)

    # reverse proxy for the drone camera feed (registered before the SPA mount)
    add_camera_routes(app)

    if spa_built:
        # Mounted last so the API routes above take precedence. html=True serves
        # index.html at "/" and falls back to it for client-side routes, while
        # hashed assets are served straight from dist/assets/.
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="spa")

    return app


async def _handle_ws(app: FastAPI, websocket: WebSocket) -> None:
    settings: Settings = app.state.settings
    hub: Hub = app.state.hub
    udp: UdpLink = app.state.udp
    session: Session = app.state.session

    await websocket.accept()

    # --- login gate: no relay, no telemetry until the password authenticates ---
    try:
        token = await _authenticate(websocket, udp, session, settings)
    except WebSocketDisconnect:
        return
    if token is None:
        return

    # prime the UI with the most recent telemetry snapshot immediately
    latest = udp.latest_telemetry
    if latest is not None:
        await websocket.send_text(json.dumps(latest, separators=(",", ":")))

    reader = asyncio.create_task(_read_loop(websocket, udp, session, token))
    writer = asyncio.create_task(hub.serve(websocket))
    try:
        done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.debug("ws task ended with %r", exc)
    finally:
        # drop the authenticated drone link once nobody is watching
        if hub.client_count == 0:
            session.clear()
            log.info("last client gone; drone session cleared")


async def _authenticate(
    websocket: WebSocket, udp: UdpLink, session: Session, settings: Settings
) -> str | None:
    """Run the login handshake. Returns the validated token, or None to close.

    The first client message must be an ``auth`` packet. Its password is used as
    the drone token to probe each candidate IP; the drone that replies with
    telemetry accepted it. Success pins the session; failure invites a retry.
    """
    while True:
        raw = await websocket.receive_text()  # WebSocketDisconnect -> caller returns
        try:
            data = json.loads(raw)
            auth = AuthIn.model_validate(data)
        except (ValueError, ValidationError):
            await websocket.send_text(json.dumps({"t": "auth_required"}))
            continue

        ip = await _probe_candidates(udp, settings, auth.password)
        if ip is not None:
            session.set(auth.password, (ip, settings.airborne_port))
            await websocket.send_text(json.dumps({"t": "auth_ok", "airborne": ip}))
            log.info("client authenticated to drone %s", ip)
            return auth.password

        await websocket.send_text(
            json.dumps({"t": "auth_fail", "reason": "no drone accepted that password"})
        )
        log.info("login rejected (password matched no candidate drone)")


async def _probe_candidates(udp: UdpLink, settings: Settings, password: str) -> str | None:
    for ip in settings.candidate_ips:
        if await udp.probe(ip, password, settings.auth_probe_timeout_s):
            return ip
    return None


async def _read_loop(
    websocket: WebSocket, udp: UdpLink, session: Session, token: str
) -> None:
    while True:
        raw = await websocket.receive_text()  # raises WebSocketDisconnect on close
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("t") == "auth":
            continue  # already authenticated on this socket; ignore repeats
        try:
            message = _client_adapter.validate_python(data)
        except ValidationError:
            log.debug("dropping invalid client message")
            continue
        if session.remote is None:
            continue  # session cleared underneath us; nothing to send to
        outbound = message.model_dump()
        outbound["token"] = token  # inject the operator's token server-side
        udp.send(outbound, session.remote)


app = create_app()
