"""Sensor streaming: parsing, gating, and the property that it never transmits.

The sensor feed exists for research and runs beside the control path, not in it.
Two things therefore matter more than the data itself:

1. It is gated on the drone session, like the cockpit and the camera. No login,
   no sensor data.
2. It is receive-only. The socket that listens for the aircraft's forwarded
   MAVLink must never send anything back, because the aircraft is flying.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.sensors import GROUP_RATES, SensorHub


def mav():
    """A MAVLink encoder standing in for the aircraft."""
    from pymavlink.dialects.v20 import ardupilotmega as dialect

    class _Sink:
        def __init__(self) -> None:
            self.buf = bytearray()

        def write(self, data) -> None:
            self.buf.extend(data)

    sink = _Sink()
    link = dialect.MAVLink(sink, srcSystem=1, srcComponent=1)
    return link, sink


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def test_attitude_is_reported_in_degrees() -> None:
    link, sink = mav()
    link.attitude_send(0, 0.5, -0.25, 1.0, 0.1, -0.1, 0.0)  # radians on the wire

    hub = SensorHub()
    assert hub.feed(bytes(sink.buf)) >= 1

    data = hub.snapshot("attitude")["data"]
    assert data["roll"] == pytest.approx(28.65, abs=0.05)
    assert data["pitch"] == pytest.approx(-14.32, abs=0.05)
    assert 0 <= data["yaw"] <= 360


def test_rangefinder_reaches_the_position_group() -> None:
    link, sink = mav()
    link.rangefinder_send(1.37, 0.0)

    hub = SensorHub()
    hub.feed(bytes(sink.buf))
    assert hub.snapshot("position")["data"]["rangefinder_m"] == pytest.approx(1.37)


def test_motor_outputs_are_carried() -> None:
    link, sink = mav()
    link.servo_output_raw_send(0, 0, 1500, 1600, 1400, 1550, 0, 0, 0, 0)

    hub = SensorHub()
    hub.feed(bytes(sink.buf))
    assert hub.snapshot("motors")["data"]["outputs"] == [1500, 1600, 1400, 1550]


def test_all_group_merges_every_other_group() -> None:
    link, sink = mav()
    link.attitude_send(0, 0.1, 0.1, 0.1, 0, 0, 0)
    link.rangefinder_send(2.0, 0.0)

    hub = SensorHub()
    hub.feed(bytes(sink.buf))

    everything = hub.snapshot("all")["data"]
    assert everything["attitude"]["roll"] is not None
    assert everything["position"]["rangefinder_m"] == pytest.approx(2.0)


def test_a_corrupt_datagram_does_not_raise() -> None:
    hub = SensorHub()
    assert hub.feed(b"\xfd\x00\x00garbage not mavlink") == 0
    # and the hub still works afterwards
    link, sink = mav()
    link.attitude_send(0, 0.2, 0, 0, 0, 0, 0)
    assert hub.feed(bytes(sink.buf)) >= 1


def test_snapshot_reports_link_age() -> None:
    hub = SensorHub()
    assert hub.snapshot("attitude")["link_age_ms"] is None    # nothing has arrived
    link, sink = mav()
    link.attitude_send(0, 0, 0, 0, 0, 0, 0)
    hub.feed(bytes(sink.buf))
    assert hub.snapshot("attitude")["link_age_ms"] is not None


# --------------------------------------------------------------------------- #
# the two properties that matter
# --------------------------------------------------------------------------- #

def test_the_receiver_never_transmits() -> None:
    """The UDP endpoint must be receive-only: the aircraft is flying.

    Checked structurally rather than by observation, because "it did not send
    anything this time" is weaker than "it has no way to send anything".
    """
    import ast
    import inspect

    from app import sensors

    # Parse the module and look at actual calls, so comments and docstrings that
    # discuss sending cannot pass or fail the test by accident.
    tree = ast.parse(inspect.getsource(sensors))
    forbidden = {"sendto", "send", "send_bytes", "write", "sendall", "send_text"}
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    offenders = called & forbidden
    # send_text is how a websocket replies to a subscriber, which is outbound to
    # the browser, not to the aircraft. Everything else would be a transmit path.
    offenders.discard("send_text")
    assert not offenders, f"sensor module can transmit: {sorted(offenders)}"


@pytest.fixture()
def app_client(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUND_SENSORS_ENABLED", "false")   # no socket in tests
    monkeypatch.setenv("GROUND_FLEET_FILE", str(tmp_path / "absent.json"))
    from app import main, settings

    settings.get_settings.cache_clear()
    try:
        yield main.create_app()
    finally:
        settings.get_settings.cache_clear()


def test_sensor_stream_requires_a_drone_session(app_client) -> None:
    with TestClient(app_client) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/sensors/attitude") as ws:
                ws.receive_text()


def test_sensor_stream_sends_once_logged_in(app_client) -> None:
    with TestClient(app_client) as client:
        client.app.state.session.set("token", ("127.0.0.1", 14650))
        with client.websocket_connect("/sensors/attitude") as ws:
            frame = json.loads(ws.receive_text())
    assert frame["group"] == "attitude"
    assert "ts" in frame and "data" in frame


def test_status_endpoint_lists_the_rates(app_client) -> None:
    with TestClient(app_client) as client:
        body = client.get("/sensors/status").json()
    assert body["rates_hz"] == GROUP_RATES
    assert body["authorised"] is False
