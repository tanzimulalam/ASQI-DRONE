"""Fleet screen: naming, health, and the two properties that must hold.

1. A drone's token never reaches a browser. The fleet file holds secrets so the
   ground station can run read-only health probes; the API must not leak them.
2. The aircraft being flown is never health-probed. The daemon sends telemetry to
   whoever last sent it a valid packet, so a probe would steal the cockpit's
   telemetry stream mid-flight.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import fleet as fleet_mod

PIPER_TOKEN = "piper-secret-token"
OMEGA_TOKEN = "omega-secret-token"


def _write_fleet(tmp_path: Path, drones: list[dict]) -> Path:
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps({"drones": drones}), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #

def test_load_fleet_reads_names_ips_and_tokens(tmp_path: Path) -> None:
    path = _write_fleet(tmp_path, [
        {"name": "Piper", "ip": "10.42.0.1", "token": PIPER_TOKEN},
        {"name": "Omega", "ip": "10.130.143.5", "token": OMEGA_TOKEN},
    ])
    drones = fleet_mod.load_fleet(path, ["192.0.2.1"])
    assert [(d.name, d.ip, d.token) for d in drones] == [
        ("Piper", "10.42.0.1", PIPER_TOKEN),
        ("Omega", "10.130.143.5", OMEGA_TOKEN),
    ]


def test_missing_fleet_file_falls_back_to_candidates_without_tokens(tmp_path: Path) -> None:
    drones = fleet_mod.load_fleet(tmp_path / "absent.json", ["10.42.0.1", "10.0.0.9"])
    assert [(d.name, d.ip, d.token) for d in drones] == [
        ("Drone 1", "10.42.0.1", None),
        ("Drone 2", "10.0.0.9", None),
    ]


def test_broken_fleet_file_does_not_stop_the_bridge(tmp_path: Path) -> None:
    p = tmp_path / "fleet.json"
    p.write_text("{ this is not json", encoding="utf-8")
    drones = fleet_mod.load_fleet(p, ["10.42.0.1"])
    assert [d.ip for d in drones] == ["10.42.0.1"]


def test_duplicate_names_are_rejected(tmp_path: Path) -> None:
    path = _write_fleet(tmp_path, [
        {"name": "Piper", "ip": "10.42.0.1"},
        {"name": "piper", "ip": "10.42.0.2"},
    ])
    # Two aircraft answering to one name would make "Fly Piper" ambiguous.
    drones = fleet_mod.load_fleet(path, ["10.9.9.9"])
    assert [d.ip for d in drones] == ["10.9.9.9"]


def test_an_aircraft_in_build_needs_no_address(tmp_path: Path) -> None:
    path = _write_fleet(tmp_path, [
        {"name": "Piper", "ip": "10.42.0.1"},
        {"name": "Vulkan", "ready": False},
    ])
    drones = fleet_mod.load_fleet(path, ["192.0.2.1"])
    vulkan = fleet_mod.find(drones, "vulkan")
    assert vulkan.ready is False and vulkan.ip == ""


def test_a_ready_aircraft_without_an_address_is_rejected(tmp_path: Path) -> None:
    path = _write_fleet(tmp_path, [{"name": "Piper"}])
    # "Ready" with nowhere to reach it is a broken file, not an aircraft in build.
    drones = fleet_mod.load_fleet(path, ["192.0.2.1"])
    assert [d.ip for d in drones] == ["192.0.2.1"]


def test_find_is_case_insensitive() -> None:
    fleet = [fleet_mod.Drone("Piper", "10.42.0.1"), fleet_mod.Drone("Omega", "10.130.143.5")]
    assert fleet_mod.find(fleet, "OMEGA").ip == "10.130.143.5"
    assert fleet_mod.find(fleet, " piper ").ip == "10.42.0.1"
    assert fleet_mod.find(fleet, "nobody") is None


def test_summary_carries_only_whitelisted_fields() -> None:
    tlm = {"t": "tlm", "batt_v": 15.6, "mode": "LOITER", "token": "leaked!", "junk": 1}
    s = fleet_mod.summarise(tlm)
    assert s["batt_v"] == 15.6 and s["mode"] == "LOITER"
    assert "token" not in s and "junk" not in s and "t" not in s


# --------------------------------------------------------------------------- #
# the API
# --------------------------------------------------------------------------- #

@pytest.fixture()
def fleet_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _write_fleet(tmp_path, [
        {"name": "Piper", "ip": "127.0.0.1", "token": PIPER_TOKEN},
        {"name": "Omega", "ip": "127.0.0.2", "token": OMEGA_TOKEN},
        {"name": "Spare", "ip": "127.0.0.3"},
        {"name": "Vulkan", "ready": False},
    ])
    monkeypatch.setenv("GROUND_FLEET_FILE", str(path))

    from app import main, settings

    settings.get_settings.cache_clear()

    async def always_reachable(ip: str, port: int = 22, timeout: float = 1.0) -> bool:
        # An aircraft in build has no address; being asked about one is a bug.
        assert ip, "tried to reach an aircraft that has no address"
        return True

    # Keep the tests off the real network.
    monkeypatch.setattr(fleet_mod, "jetson_reachable", always_reachable)
    try:
        yield main.create_app()
    finally:
        settings.get_settings.cache_clear()


def test_fleet_endpoint_never_exposes_tokens(fleet_app) -> None:
    with TestClient(fleet_app) as client:
        body = client.get("/api/fleet").text
    assert PIPER_TOKEN not in body and OMEGA_TOKEN not in body
    names = [d["name"] for d in json.loads(body)["drones"]]
    assert names == ["Piper", "Omega", "Spare", "Vulkan"]


def test_an_aircraft_in_build_is_listed_but_never_reached(fleet_app) -> None:
    # The fixture's reachability stub asserts if it is handed an empty address.
    with TestClient(fleet_app) as client:
        drones = {d["name"]: d for d in client.get("/api/fleet").json()["drones"]}
    v = drones["Vulkan"]
    assert v["ready"] is False
    assert v["reachable"] is False
    assert v["can_check_health"] is False
    assert v["ip"] is None


def test_closing_the_browser_ends_the_drone_session(fleet_app) -> None:
    """Regression: the drone session must clear when the last browser leaves.

    The websocket handler cancelled the hub's writer task and then counted
    clients straight away. Cancellation is asynchronous: the hub removes a client
    in its own finally block, which had not run yet. So the count read 1, the
    session was never cleared, and nothing checked again. After a single login an
    aircraft stayed "in session" for good, and its health checks were refused
    because the bridge believed it was still being flown. It also meant the
    fleet screen's Back to Fleet could not actually end a session.
    """
    with TestClient(fleet_app) as client:
        app = client.app

        async def accept(ip: str, password: str, timeout: float) -> bool:
            return True

        app.state.udp.probe = accept

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"t": "auth", "password": OMEGA_TOKEN, "drone": "Omega"}))
            assert json.loads(ws.receive_text())["t"] == "auth_ok"
            assert app.state.session.active is True

        # The handler's cleanup runs after the socket closes; allow it a moment.
        for _ in range(60):
            if not app.state.session.active:
                break
            time.sleep(0.05)
        assert app.state.session.active is False, "drone session outlived the last browser"


def test_an_aircraft_in_build_cannot_be_health_checked(fleet_app) -> None:
    with TestClient(fleet_app) as client:
        assert client.post("/api/fleet/vulkan/health").status_code == 409


def test_health_check_never_probes_the_aircraft_being_flown(fleet_app) -> None:
    with TestClient(fleet_app) as client:
        app = client.app

        async def must_not_probe(*_a, **_k):
            raise AssertionError("probed the aircraft being flown")

        app.state.udp.probe_telemetry = must_not_probe
        app.state.session.set(PIPER_TOKEN, ("127.0.0.1", 14650))

        resp = client.post("/api/fleet/piper/health")

    assert resp.status_code == 200
    assert resp.json()["active"] is True


def test_health_check_probes_an_idle_aircraft(fleet_app) -> None:
    with TestClient(fleet_app) as client:
        app = client.app
        calls: list[tuple[str, str]] = []

        async def fake_probe(ip: str, token: str, timeout: float):
            calls.append((ip, token))
            return {"t": "tlm", "batt_v": 16.1, "mode": "STABILIZE", "armed": False}

        app.state.udp.probe_telemetry = fake_probe
        resp = client.post("/api/fleet/omega/health")

    assert calls == [("127.0.0.2", OMEGA_TOKEN)]
    data = resp.json()
    assert data["health"]["batt_v"] == 16.1
    assert OMEGA_TOKEN not in resp.text


def test_health_check_unknown_aircraft_is_404(fleet_app) -> None:
    with TestClient(fleet_app) as client:
        assert client.post("/api/fleet/nobody/health").status_code == 404


def test_health_check_without_a_token_is_refused(fleet_app) -> None:
    with TestClient(fleet_app) as client:
        assert client.post("/api/fleet/spare/health").status_code == 409
