"""Controller tick behavior around RC-pilot takeover.

When the vehicle leaves a web-pilotable mode (safety pilot flips the transmitter
mode switch), the very next tick must release the RC override so the transmitter
gets stick authority immediately instead of after the FC's RC_OVERRIDE_TIME.
"""
from __future__ import annotations

from dataclasses import replace

from airborne_daemon import rc
from airborne_daemon.controller import Controller
from airborne_daemon.modes import mode_number
from airborne_daemon.settings import Settings
from airborne_daemon.state import ControlState, EventBus, VehicleState

ADDR = ("127.0.0.1", 5000)


class FakeLink:
    calibration = rc.DEFAULT_FALLBACK_CAL

    def __init__(self) -> None:
        self.overrides: list[tuple[int, int, int, int]] = []
        self.releases = 0
        self.modes: list[int] = []

    def send_rc_override(self, ch1, ch2, ch3, ch4) -> None:
        self.overrides.append((ch1, ch2, ch3, ch4))

    def release_rc_override(self) -> None:
        self.releases += 1

    def set_mode(self, mode_num: int) -> None:
        self.modes.append(mode_num)


def _make(mode: str = "LOITER", armed: bool = True, **overrides):
    settings = Settings()
    if overrides:
        settings = replace(settings, **overrides)
    link = FakeLink()
    vehicle = VehicleState()
    control = ControlState()
    events = EventBus()
    ctrl = Controller(settings, link, vehicle, control, events)  # type: ignore[arg-type]
    vehicle.update_heartbeat(armed=armed, mode_num=mode_number(mode))
    control.accept_control(0, rc.Sticks(roll=0.5), ADDR)
    return ctrl, link, vehicle, control, events


def test_allowed_mode_sends_overrides() -> None:
    ctrl, link, _, _, _ = _make(mode="LOITER")
    ctrl._tick()
    assert len(link.overrides) == 1
    assert link.releases == 0


def test_mode_takeover_releases_override_once() -> None:
    ctrl, link, vehicle, _, events = _make(mode="LOITER")
    ctrl._tick()
    assert link.overrides  # piloting

    # safety pilot flips the transmitter mode switch to STABILIZE
    vehicle.update_heartbeat(armed=True, mode_num=mode_number("STABILIZE"))
    ctrl._tick()
    assert link.releases == 1
    assert any(e["kind"] == "pilot_takeover" for e in events.recent(5))

    # subsequent idle ticks stay quiet: no re-release, no new overrides
    ctrl._tick()
    ctrl._tick()
    assert link.releases == 1
    assert len(link.overrides) == 1


def test_disarm_releases_override_without_takeover_event() -> None:
    ctrl, link, vehicle, _, events = _make(mode="LOITER")
    ctrl._tick()
    vehicle.update_heartbeat(armed=False, mode_num=mode_number("LOITER"))
    ctrl._tick()
    assert link.releases == 1
    assert not any(e["kind"] == "pilot_takeover" for e in events.recent(5))


def test_idle_before_ever_piloting_never_releases() -> None:
    ctrl, link, _, _, _ = _make(mode="STABILIZE")
    ctrl._tick()
    ctrl._tick()
    assert link.overrides == []
    assert link.releases == 0


# --- stick-touch takeover on mirrored transmitter channels ------------------ #
WATCH = {"pilot_takeover_channels": (9, 10), "pilot_takeover_dz_us": 50}


def _rc_in(ch9: int, ch10: int) -> tuple[int, ...]:
    return (1500, 1500, 1000, 1500, 1800, 0, 0, 0, ch9, ch10)


def test_stick_touch_releases_override_and_latches() -> None:
    ctrl, link, vehicle, _, events = _make(mode="LOITER", **WATCH)
    vehicle.update(rc_in=_rc_in(1500, 1000))
    ctrl._tick()  # captures baseline, pilots
    ctrl._tick()  # unchanged sticks keep piloting
    assert len(link.overrides) == 2

    vehicle.update(rc_in=_rc_in(1600, 1000))  # pilot nudges mirrored roll
    ctrl._tick()
    assert link.releases == 1
    assert len(link.overrides) == 2  # no more GUI sticks after the touch
    assert ctrl.pilot_takeover
    assert any(e["kind"] == "pilot_takeover" for e in events.recent(5))

    ctrl._tick()  # latched: stays idle, no re-release
    assert link.releases == 1


def test_takeover_works_for_non_centering_throttle() -> None:
    # baseline is captured per session, so a ratchet throttle resting at 1000
    # only trips when it MOVES, not for being away from 1500
    ctrl, link, vehicle, _, _ = _make(mode="LOITER", **WATCH)
    vehicle.update(rc_in=_rc_in(1500, 1000))
    ctrl._tick()
    ctrl._tick()
    assert not ctrl.pilot_takeover

    vehicle.update(rc_in=_rc_in(1500, 1120))
    ctrl._tick()
    assert ctrl.pilot_takeover


def test_jitter_within_deadzone_does_not_trip() -> None:
    ctrl, _, vehicle, _, _ = _make(mode="LOITER", **WATCH)
    vehicle.update(rc_in=_rc_in(1500, 1000))
    ctrl._tick()
    vehicle.update(rc_in=_rc_in(1540, 1010))  # within 50 us deadzone
    ctrl._tick()
    assert not ctrl.pilot_takeover


def test_no_receiver_data_never_trips() -> None:
    ctrl, link, _, _, _ = _make(mode="LOITER", **WATCH)  # rc_in stays ()
    ctrl._tick()
    ctrl._tick()
    assert not ctrl.pilot_takeover
    assert len(link.overrides) == 2


def test_resume_returns_control_and_recaptures_baseline() -> None:
    ctrl, link, vehicle, _, _ = _make(mode="LOITER", **WATCH)
    vehicle.update(rc_in=_rc_in(1500, 1000))
    ctrl._tick()
    vehicle.update(rc_in=_rc_in(1700, 1000))
    ctrl._tick()
    assert ctrl.pilot_takeover

    ctrl._clear_pilot_takeover("operator resume")
    vehicle.update(rc_in=_rc_in(1500, 1000))  # pilot let go before resume
    ctrl._tick()  # recaptures baseline at neutral
    ctrl._tick()
    assert not ctrl.pilot_takeover
    assert len(link.overrides) == 3


def test_takeover_cleared_on_disarm() -> None:
    ctrl, _, vehicle, _, events = _make(mode="LOITER", **WATCH)
    vehicle.update(rc_in=_rc_in(1500, 1000))
    ctrl._tick()
    vehicle.update(rc_in=_rc_in(1700, 1000))
    ctrl._tick()
    assert ctrl.pilot_takeover

    vehicle.update_heartbeat(armed=False, mode_num=mode_number("LOITER"))
    ctrl._tick()
    assert not ctrl.pilot_takeover
    assert any(e["kind"] == "pilot_takeover_clear" for e in events.recent(5))
