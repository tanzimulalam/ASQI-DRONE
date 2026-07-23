"""Unit tests for camera auto-detection helpers and settings validation.

Pure logic only — no real camera or OpenCV device access is required. (The module
imports cv2, which JetPack provides on the Jetson.)
"""
from __future__ import annotations

import pytest

from camera_daemon.detect import _device_index, candidate_devices
from camera_daemon.settings import CameraSettings


def test_device_index_parsing() -> None:
    assert _device_index("/dev/video0") == 0
    assert _device_index("/dev/video11") == 11
    assert _device_index("3") == 3
    assert _device_index("auto") is None
    assert _device_index("/dev/ttyACM0") is None


def test_explicit_device_is_only_candidate() -> None:
    s = CameraSettings(device="/dev/video2")
    assert candidate_devices(s) == ["/dev/video2"]


def test_auto_falls_back_to_indices_when_no_nodes(monkeypatch) -> None:
    # no /dev/video* present -> probe indices 0..9
    monkeypatch.setattr("camera_daemon.detect.glob.glob", lambda pat: [])
    s = CameraSettings(device="auto")
    assert candidate_devices(s) == [str(i) for i in range(10)]


def test_auto_sorts_device_nodes_numerically(monkeypatch) -> None:
    monkeypatch.setattr(
        "camera_daemon.detect.glob.glob",
        lambda pat: ["/dev/video10", "/dev/video2", "/dev/video1"],
    )
    s = CameraSettings(device="auto")
    assert candidate_devices(s) == ["/dev/video1", "/dev/video2", "/dev/video10"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": 0},
        {"port": 70000},
        {"width": 8},
        {"height": 5000},
        {"fps": 0},
        {"fps": 200},
        {"jpeg_quality": 0},
        {"jpeg_quality": 101},
        {"retry_interval_s": 0},
        {"read_fail_limit": 0},
    ],
)
def test_invalid_settings_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        CameraSettings(**kwargs)


def test_defaults_are_valid() -> None:
    s = CameraSettings()
    assert s.port == 8090
    assert (s.width, s.height) == (640, 480)
    assert s.raw_passthrough is True
