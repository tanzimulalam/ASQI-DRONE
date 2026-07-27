"""Unit tests for camera auto-detection helpers and settings validation.

Pure logic only — no real camera or OpenCV device access is required. (The module
imports cv2, which JetPack provides on the Jetson.)
"""
from __future__ import annotations

import numpy as np
import pytest

from camera_daemon.capture import _is_encoded_buffer
from camera_daemon.detect import _device_index, candidate_devices, gst_pipeline
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
    assert s.backend == "auto"


# ---- capture backend selection ----


def test_backend_flags() -> None:
    assert (CameraSettings(backend="auto").try_gstreamer,
            CameraSettings(backend="auto").try_v4l2) == (True, True)
    assert CameraSettings(backend="gstreamer").try_v4l2 is False
    assert CameraSettings(backend="v4l2").try_gstreamer is False


def test_unknown_backend_rejected() -> None:
    with pytest.raises(ValueError):
        CameraSettings(backend="nvarguscamerasrc")


def test_gst_pipeline_override_must_have_appsink() -> None:
    with pytest.raises(ValueError):
        CameraSettings(gst_pipeline="v4l2src device=/dev/video0 ! fakesink")


# ---- gstreamer pipeline construction ----


def test_gst_pipeline_requests_jpeg_and_drops_stale_frames() -> None:
    s = CameraSettings(width=1280, height=720, fps=15)
    p = gst_pipeline("/dev/video1", s)
    assert "v4l2src device=/dev/video1" in p
    assert "image/jpeg,width=1280,height=720,framerate=15/1" in p
    # the latency fix: keep only the newest frame rather than queueing a backlog
    assert "max-buffers=1" in p and "drop=true" in p


def test_gst_pipeline_accepts_bare_index() -> None:
    assert "device=/dev/video3" in gst_pipeline("3", CameraSettings())


def test_gst_pipeline_override_wins() -> None:
    custom = "v4l2src ! nvjpegenc ! appsink"
    assert gst_pipeline("/dev/video0", CameraSettings(gst_pipeline=custom)) == custom


def test_gst_pipeline_refuses_undeviceable_candidates() -> None:
    # Nothing we can name a /dev node for -> no pipeline, so the V4L2 path handles it.
    # Also keeps unvetted strings from being interpolated into the pipeline.
    assert gst_pipeline("/dev/ttyACM0", CameraSettings()) is None
    assert gst_pipeline("video0 ! fakesink ! ", CameraSettings()) is None


# ---- encoded-buffer detection ----


def test_v4l2_passthrough_buffer_is_encoded() -> None:
    # CONVERT_RGB off -> flat 1-D byte buffer
    assert _is_encoded_buffer(np.zeros(4096, dtype=np.uint8)) is True


def test_gstreamer_jpeg_row_is_encoded() -> None:
    # OpenCV's GStreamer backend returns the same JPEG bytes as a single (1, N) row.
    # Misreading this as an image would send it to imencode, which would "succeed"
    # and emit a one-pixel-tall JPEG instead of the camera's frame.
    assert _is_encoded_buffer(np.zeros((1, 4096), dtype=np.uint8)) is True


@pytest.mark.parametrize("shape", [(480, 640, 3), (480, 640)])
def test_real_images_are_not_encoded_buffers(shape) -> None:
    assert _is_encoded_buffer(np.zeros(shape, dtype=np.uint8)) is False
