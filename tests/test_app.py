"""Integration tests for the FastAPI bridge using a real loopback UDP capture."""
from __future__ import annotations

import json
import socket

import pytest
from fastapi.testclient import TestClient

TOKEN = "testtok"
UDP_PORT = 14799


@pytest.fixture()
def capture(monkeypatch: pytest.MonkeyPatch):
    """A UDP socket standing in for the airborne daemon, plus a configured app."""
    monkeypatch.setenv("GROUND_AIRBORNE_IP", "127.0.0.1")
    monkeypatch.setenv("GROUND_AIRBORNE_PORT", str(UDP_PORT))
    monkeypatch.setenv("GROUND_SESSION_TOKEN", TOKEN)

    from app import main, settings

    settings.get_settings.cache_clear()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", UDP_PORT))
    sock.settimeout(2.0)
    try:
        yield main.create_app(), sock
    finally:
        sock.close()
        settings.get_settings.cache_clear()


def _recv_of_type(sock: socket.socket, wanted: str) -> dict:
    """Read datagrams until one of type ``wanted`` arrives (skips keepalives)."""
    for _ in range(10):
        data, _addr = sock.recvfrom(2048)
        msg = json.loads(data.decode())
        if msg.get("t") == wanted:
            return msg
    raise AssertionError(f"no {wanted!r} datagram received")


def test_readyz(capture) -> None:
    app, _ = capture
    with TestClient(app) as client:
        assert client.get("/readyz").json() == {"ready": True}


def test_healthz_reports_no_telemetry(capture) -> None:
    app, _ = capture
    with TestClient(app) as client:
        body = client.get("/healthz").json()
        assert body["status"] == "degraded"
        assert body["telemetry_age_ms"] is None
        assert body["clients"] == 0


def test_control_message_forwarded_with_token(capture) -> None:
    app, sock = capture
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"t": "ctrl", "seq": 1, "ts": 0, "roll": 0.3, "pitch": 0, "thr": 0.5, "yaw": 0}))
            msg = _recv_of_type(sock, "ctrl")
    assert msg["token"] == TOKEN      # secret injected server-side
    assert msg["thr"] == 0.5
    assert msg["roll"] == 0.3


def test_keepalive_datagrams_are_sent(capture) -> None:
    app, sock = capture
    with TestClient(app):
        msg = _recv_of_type(sock, "hb")
    assert msg["token"] == TOKEN


def test_invalid_ws_message_is_dropped_not_forwarded(capture) -> None:
    app, sock = capture
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"t": "ctrl", "seq": 0, "roll": 9.0}))  # out of range
            # only keepalives should appear; no ctrl datagram
            sock.settimeout(1.5)
            with pytest.raises(AssertionError):
                _recv_of_type(sock, "ctrl")
