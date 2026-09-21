"""Fleet screen: naming, health, and the two properties that must hold.

1. A drone's token never reaches a browser. The fleet file holds secrets so the
   ground station can run read-only health probes; the API must not leak them.
2. The aircraft being flown is never health-probed. The daemon sends telemetry to
   whoever last sent it a valid packet, so a probe would steal the cockpit's
   telemetry stream mid-flight.
"""
from __future__ import annotations

import json
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
    ])
    monkeypatch.setenv("GROUND_FLEET_FILE", str(path))

    from app import main, settings

    settings.get_settings.cache_clear()

    async def always_reachable(ip: str, port: int = 22, timeout: float = 1.0) -> bool:
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
    assert names == ["Piper", "Omega", "Spare"]


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
