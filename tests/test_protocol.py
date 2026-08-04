"""Validation tests for browser -> bridge messages."""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.protocol import AuthIn, ClientMessage, CommandIn, ControlIn, HeartbeatIn

adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def test_valid_control() -> None:
    m = adapter.validate_python({"t": "ctrl", "seq": 3, "ts": 1, "roll": 0.5, "pitch": -1, "thr": 0, "yaw": 0})
    assert isinstance(m, ControlIn)
    assert m.roll == 0.5


def test_valid_command_and_hb() -> None:
    assert isinstance(adapter.validate_python({"t": "cmd", "cmd": "arm"}), CommandIn)
    assert isinstance(adapter.validate_python({"t": "hb"}), HeartbeatIn)


def test_valid_takeoff() -> None:
    m = adapter.validate_python({"t": "cmd", "cmd": "takeoff", "alt": 5.0})
    assert isinstance(m, CommandIn)
    assert m.alt == 5.0


@pytest.mark.parametrize(
    "payload",
    [
        {"t": "cmd", "cmd": "takeoff"},                  # missing alt
        {"t": "cmd", "cmd": "takeoff", "alt": 0},        # non-positive alt
        {"t": "cmd", "cmd": "takeoff", "alt": 999},      # above wire cap
        {"t": "cmd", "cmd": "arm", "alt": 5.0},          # alt only valid for takeoff
    ],
)
def test_invalid_takeoff_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"t": "ctrl", "seq": 0, "roll": 2.0},          # axis out of range
        {"t": "ctrl", "seq": -1},                       # negative seq
        {"t": "cmd", "cmd": "explode"},                 # unknown command
        {"t": "ctrl", "seq": 0, "extra": 1},            # extra field forbidden
        {"t": "nope"},                                   # unknown discriminator
        {"seq": 0},                                      # missing type
    ],
)
def test_invalid_messages_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(payload)


def test_auth_message_validates() -> None:
    a = AuthIn.model_validate({"t": "auth", "password": "hunter2"})
    assert a.password == "hunter2"


@pytest.mark.parametrize(
    "payload",
    [
        {"t": "auth"},                              # missing password
        {"t": "auth", "password": ""},              # empty password
        {"t": "auth", "password": "x", "extra": 1},  # extra field forbidden
    ],
)
def test_auth_message_rejects_bad(payload: dict) -> None:
    with pytest.raises(ValidationError):
        AuthIn.model_validate(payload)


def test_auth_is_not_a_forwardable_client_message() -> None:
    # auth must be handled by the bridge, never validated into the relay union
    with pytest.raises(ValidationError):
        adapter.validate_python({"t": "auth", "password": "x"})


def test_control_axis_defaults_zero() -> None:
    m = adapter.validate_python({"t": "ctrl", "seq": 1})
    assert (m.roll, m.pitch, m.thr, m.yaw) == (0.0, 0.0, 0.0, 0.0)
