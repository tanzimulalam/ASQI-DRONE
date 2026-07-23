"""Unit tests for the link watchdog and failsafe decision logic."""
from __future__ import annotations

import pytest

from airborne_daemon.controller import (
    ControlAction,
    LinkPhase,
    WatchdogThresholds,
    classify_phase,
    decide_action,
)

TH = WatchdogThresholds(degraded_ms=150, stale_ms=350, failsafe_ms=600)


@pytest.mark.parametrize(
    "age,expected",
    [
        (0, LinkPhase.NOMINAL),
        (149, LinkPhase.NOMINAL),
        (150, LinkPhase.DEGRADED),
        (349, LinkPhase.DEGRADED),
        (350, LinkPhase.STALE),
        (599, LinkPhase.STALE),
        (600, LinkPhase.FAILSAFE),
        (10_000, LinkPhase.FAILSAFE),
    ],
)
def test_classify_phase_boundaries(age: int, expected: LinkPhase) -> None:
    assert classify_phase(age, TH) is expected


def _decide(age, armed=True, mode_allowed=True, latched=False):
    return decide_action(
        age, armed=armed, mode_allowed=mode_allowed, failsafe_latched=latched, thresholds=TH
    )


def test_fresh_link_sends_sticks() -> None:
    assert _decide(50) is ControlAction.SEND_STICKS
    assert _decide(200) is ControlAction.SEND_STICKS  # degraded still flies


def test_stale_link_holds_neutral() -> None:
    assert _decide(400) is ControlAction.HOLD_NEUTRAL


def test_lost_link_trips_failsafe_when_armed() -> None:
    assert _decide(700) is ControlAction.TRIP_FAILSAFE


def test_disarmed_is_always_idle() -> None:
    assert _decide(50, armed=False) is ControlAction.IDLE
    assert _decide(700, armed=False) is ControlAction.IDLE


def test_disallowed_mode_never_sends_sticks() -> None:
    # e.g. Stabilize: refuse overrides even with a fresh link ...
    assert _decide(50, mode_allowed=False) is ControlAction.IDLE
    # ... but a lost link while armed still trips the failsafe regardless of mode
    assert _decide(700, mode_allowed=False) is ControlAction.TRIP_FAILSAFE


def test_latched_failsafe_idles_until_cleared() -> None:
    assert _decide(50, latched=True) is ControlAction.IDLE
    assert _decide(700, latched=True) is ControlAction.IDLE


def test_failsafe_threshold_precedes_mode_check() -> None:
    # ordering guarantee: failsafe wins over the mode gate
    assert _decide(600, mode_allowed=False) is ControlAction.TRIP_FAILSAFE
