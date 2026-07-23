"""Unit tests for wire-protocol parsing and validation."""
from __future__ import annotations

import json

import pytest

from airborne_daemon import protocol
from airborne_daemon.protocol import CommandPacket, ControlPacket, HeartbeatPacket, ProtocolError


def _ctrl(**over) -> bytes:
    base = {"t": "ctrl", "seq": 1, "ts": 10, "roll": 0.0, "pitch": 0.0, "thr": 0.0, "yaw": 0.0, "token": "tok"}
    base.update(over)
    return json.dumps(base).encode()


def test_parse_control_packet() -> None:
    pkt, token = protocol.parse_packet(_ctrl(seq=7, thr=0.25))
    assert isinstance(pkt, ControlPacket)
    assert pkt.seq == 7
    assert token == "tok"
    assert pkt.sticks.throttle == 0.25


def test_parse_command_packet() -> None:
    pkt, _ = protocol.parse_packet(b'{"t":"cmd","cmd":"set_mode","mode":"LOITER","token":"x"}')
    assert isinstance(pkt, CommandPacket)
    assert pkt.cmd == "set_mode"
    assert pkt.mode == "LOITER"


def test_parse_takeoff_command() -> None:
    pkt, _ = protocol.parse_packet(b'{"t":"cmd","cmd":"takeoff","alt":5.5,"token":"x"}')
    assert isinstance(pkt, CommandPacket)
    assert pkt.cmd == "takeoff"
    assert pkt.alt == 5.5


@pytest.mark.parametrize(
    "raw",
    [
        b'{"t":"cmd","cmd":"takeoff"}',                    # missing alt
        b'{"t":"cmd","cmd":"takeoff","alt":0}',            # non-positive
        b'{"t":"cmd","cmd":"takeoff","alt":999}',          # above hard cap
        b'{"t":"cmd","cmd":"takeoff","alt":"high"}',       # not a number
        b'{"t":"cmd","cmd":"arm","alt":5}',                # alt only valid for takeoff
    ],
)
def test_malformed_takeoff_rejected(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        protocol.parse_packet(raw)


def test_parse_heartbeat() -> None:
    pkt, tok = protocol.parse_packet(b'{"t":"hb","token":"x"}')
    assert isinstance(pkt, HeartbeatPacket)
    assert tok == "x"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b'{"no":"type"}',
        b'{"t":"ctrl"}',                       # missing fields
        b'{"t":"ctrl","seq":-1,"roll":0,"pitch":0,"thr":0,"yaw":0}',
        b'{"t":"ctrl","seq":1,"roll":2,"pitch":0,"thr":0,"yaw":0}',   # out of range
        b'{"t":"cmd","cmd":"explode"}',        # unknown command
        b'{"t":"bogus"}',
    ],
)
def test_malformed_packets_rejected(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        protocol.parse_packet(raw)


def test_axis_epsilon_is_clamped_not_rejected() -> None:
    # a value a hair over 1.0 from float round-trip is clamped, not rejected
    pkt, _ = protocol.parse_packet(_ctrl(roll=1.0005))
    assert isinstance(pkt, ControlPacket)
    assert pkt.sticks.roll == 1.0


def test_missing_token_is_none() -> None:
    pkt, token = protocol.parse_packet(b'{"t":"hb"}')
    assert token is None


def test_telemetry_roundtrips_to_json() -> None:
    tlm = protocol.Telemetry(
        ts=1, connected=True, armed=False, mode="LOITER", mode_num=5, hb_age_ms=20,
        gps_fix=3, sats=18, batt_v=15.8, batt_pct=87, current_a=2.1, alt=1.2,
        gspeed=0.3, ekf_ok=True, ctrl_age_ms=18, link_phase="nominal", failsafe=False,
        allowed_modes=["LOITER"], statustext="", events=[], proto=1,
    )
    decoded = json.loads(tlm.to_json())
    assert decoded["t"] == "tlm"
    assert decoded["mode"] == "LOITER"
    assert decoded["proto"] == 1
