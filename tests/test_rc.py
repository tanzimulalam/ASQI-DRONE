"""Unit tests for RC calibration and stick-to-PWM scaling."""
from __future__ import annotations

import math

import pytest

from airborne_daemon import rc


def test_channel_cal_rejects_bad_ranges() -> None:
    with pytest.raises(ValueError):
        rc.ChannelCal(min=2000, max=1000, trim=1500)
    with pytest.raises(ValueError):
        rc.ChannelCal(min=1000, max=2000, trim=2500)


def test_scale_axis_endpoints_and_center() -> None:
    cal = rc.ChannelCal(min=1000, max=2000, trim=1500)
    assert rc.scale_axis(-1.0, cal) == 1000
    assert rc.scale_axis(0.0, cal) == 1500
    assert rc.scale_axis(1.0, cal) == 2000


def test_scale_axis_respects_asymmetric_trim() -> None:
    # yaw channel from the real vehicle: min 1017, trim 1503
    cal = rc.ChannelCal(min=1017, max=2000, trim=1503)
    assert rc.scale_axis(-1.0, cal) == 1017
    assert rc.scale_axis(0.0, cal) == 1503
    assert rc.scale_axis(1.0, cal) == 2000
    # halfway down should sit between trim and min, not min and midpoint
    assert rc.scale_axis(-0.5, cal) == round(1503 - 0.5 * (1503 - 1017))


def test_throttle_center_hold_uses_midpoint_not_trim() -> None:
    # throttle trim is 1000 but center-hold should pivot on the midpoint (1500)
    cal = rc.ChannelCal(min=1000, max=2000, trim=1000)
    assert rc.scale_axis(0.0, cal, center_hold=True) == 1500
    assert rc.scale_axis(-1.0, cal, center_hold=True) == 1000
    assert rc.scale_axis(1.0, cal, center_hold=True) == 2000


def test_scale_axis_clamps_out_of_range_and_nonfinite() -> None:
    cal = rc.ChannelCal(min=1000, max=2000, trim=1500)
    assert rc.scale_axis(5.0, cal) == 2000
    assert rc.scale_axis(-5.0, cal) == 1000
    assert rc.scale_axis(math.nan, cal) == 1500


def test_reversed_channel_flips_direction() -> None:
    cal = rc.ChannelCal(min=1000, max=2000, trim=1500, reversed=True)
    assert rc.scale_axis(1.0, cal) == 1000
    assert rc.scale_axis(-1.0, cal) == 2000


def test_sticks_to_pwm_channel_order() -> None:
    ch1, ch2, ch3, ch4 = rc.sticks_to_pwm(
        rc.Sticks(roll=1.0, pitch=-1.0, throttle=0.0, yaw=1.0), rc.DEFAULT_FALLBACK_CAL
    )
    assert ch1 == 2000          # roll +1
    assert ch2 == 1000          # pitch -1
    assert ch3 == 1500          # throttle center-hold
    assert ch4 == 2000          # yaw +1


def test_invert_axis() -> None:
    base = rc.sticks_to_pwm(rc.Sticks(roll=1.0), rc.DEFAULT_FALLBACK_CAL)
    inv = rc.sticks_to_pwm(rc.Sticks(roll=1.0), rc.DEFAULT_FALLBACK_CAL, invert={"roll": True})
    assert base[0] == 2000
    assert inv[0] == 1000


def test_neutral_pwm_is_centered() -> None:
    ch1, ch2, ch3, ch4 = rc.neutral_pwm(rc.DEFAULT_FALLBACK_CAL)
    assert ch1 == 1501          # roll trim
    assert ch2 == 1500          # pitch trim
    assert ch3 == 1500          # throttle midpoint (hold)
    assert ch4 == 1503          # yaw trim


def test_missing_channel_calibration_rejected() -> None:
    with pytest.raises(ValueError):
        rc.RCCalibration({1: rc.ChannelCal(1000, 2000, 1500)})
