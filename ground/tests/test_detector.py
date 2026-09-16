"""Unit tests for the ground-side detector.

Pure logic only — no CUDA, no jetson-inference, no camera. The heavy imports live
behind ``Detector.load()`` precisely so this file can run on any machine.
"""
from __future__ import annotations

import pytest

from detector.engine import format_detections
from detector.mjpeg import iter_jpegs, parse_boundary
from detector.settings import DetectorSettings
from detector.state import DetectionState

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def _part(payload: bytes, boundary: bytes = b"frame") -> bytes:
    return (b"--" + boundary + b"\r\nContent-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload + b"\r\n")


def _jpeg(body: bytes) -> bytes:
    return SOI + body + EOI


# ---- boundary parsing ----


def test_parse_boundary_variants() -> None:
    assert parse_boundary("multipart/x-mixed-replace; boundary=frame") == b"frame"
    assert parse_boundary('multipart/x-mixed-replace; boundary="frame"') == b"frame"
    assert parse_boundary("MULTIPART/X-MIXED-REPLACE; BOUNDARY=abc123") == b"abc123"


def test_parse_boundary_rejects_non_multipart() -> None:
    assert parse_boundary("image/jpeg") is None
    assert parse_boundary("") is None


# ---- multipart frame extraction ----


def test_iter_jpegs_extracts_frames() -> None:
    a, b = _jpeg(b"AAA"), _jpeg(b"BBB")
    out = list(iter_jpegs(iter([_part(a) + _part(b)])))
    assert out == [a, b]


def test_iter_jpegs_reassembles_across_chunk_boundaries() -> None:
    # A frame split mid-payload must still come out whole — chunk boundaries have
    # nothing to do with frame boundaries on a real socket.
    blob = _part(_jpeg(b"HELLO-WORLD"))
    chunks = [blob[i : i + 3] for i in range(0, len(blob), 3)]
    assert list(iter_jpegs(iter(chunks))) == [_jpeg(b"HELLO-WORLD")]


def test_iter_jpegs_discards_leading_garbage() -> None:
    frame = _jpeg(b"X")
    assert list(iter_jpegs(iter([b"HTTP/1.1 200 OK\r\n\r\n--frame\r\n\r\n" + frame]))) == [frame]


def test_iter_jpegs_waits_for_complete_frame() -> None:
    # An unterminated frame yields nothing rather than a truncated JPEG.
    assert list(iter_jpegs(iter([SOI + b"partial"]))) == []


# ---- detection formatting ----


class _FakeDetection:
    def __init__(self, cls, conf, left, top, right, bottom) -> None:
        self.ClassID, self.Confidence = cls, conf
        self.Left, self.Top, self.Right, self.Bottom = left, top, right, bottom


def _label(i: int) -> str:
    return {1: "person", 3: "car"}.get(i, f"class{i}")


def test_format_detections_converts_to_browser_shape() -> None:
    out = format_detections([_FakeDetection(1, 0.92, 10, 20, 110, 220)], _label, 20)
    # browser draws [x, y, w, h]; detectNet reports corners
    assert out == [{"bbox": [10.0, 20.0, 100.0, 200.0], "class": "person", "score": 0.92}]


def test_format_detections_sorts_by_score_and_caps() -> None:
    raw = [_FakeDetection(1, 0.3, 0, 0, 10, 10), _FakeDetection(3, 0.9, 0, 0, 10, 10),
           _FakeDetection(1, 0.6, 0, 0, 10, 10)]
    out = format_detections(raw, _label, 2)
    assert [b["score"] for b in out] == [0.9, 0.6]


def test_format_detections_drops_degenerate_boxes() -> None:
    assert format_detections([_FakeDetection(1, 0.9, 50, 50, 50, 80)], _label, 20) == []


# ---- state / long poll ----


def test_state_returns_snapshot_and_bumps_seq() -> None:
    s = DetectionState()
    s.publish([{"bbox": [0, 0, 1, 1], "class": "person", "score": 0.9}], 640, 480, 12.5, 30.0)
    snap = s.wait_for(0, timeout=0.1)
    assert snap["seq"] == 1 and snap["srcW"] == 640 and len(snap["boxes"]) == 1


def test_state_long_poll_times_out_without_error() -> None:
    s = DetectionState()
    s.publish([], 640, 480, 1.0, 1.0)
    # asking for something newer than what exists returns the current snapshot
    snap = s.wait_for(1, timeout=0.15)
    assert snap["seq"] == 1


def test_state_keeps_only_newest() -> None:
    s = DetectionState()
    s.publish([{"bbox": [0, 0, 1, 1], "class": "a", "score": 0.5}], 1, 1, 1.0, 1.0)
    s.publish([{"bbox": [0, 0, 2, 2], "class": "b", "score": 0.6}], 1, 1, 1.0, 1.0)
    snap = s.wait_for(0, timeout=0.1)
    assert snap["seq"] == 2 and snap["boxes"][0]["class"] == "b"


def test_status_omits_boxes() -> None:
    s = DetectionState()
    s.publish([{"bbox": [0, 0, 1, 1], "class": "a", "score": 0.5}], 1, 1, 1.0, 1.0)
    assert "boxes" not in s.status()


def test_state_keeps_frame_with_its_boxes() -> None:
    s = DetectionState()
    s.publish([{"bbox": [0, 0, 1, 1], "class": "a", "score": 0.5}], 1, 1, 1.0, 1.0, jpeg=b"J1")
    # a publish without a frame keeps the previous one (boxes may update faster)
    s.publish([], 1, 1, 1.0, 1.0)
    jpeg, boxes = s.frame()
    assert jpeg == b"J1" and boxes == []


def test_annotate_jpeg_draws_and_reencodes() -> None:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from detector.server import annotate_jpeg

    blank = np.zeros((120, 160, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    assert ok
    out = annotate_jpeg(buf.tobytes(), [{"bbox": [20, 20, 60, 40], "class": "person", "score": 0.9}])
    assert out is not None and out[:2] == b"\xff\xd8"
    drawn = cv2.imdecode(np.frombuffer(out, dtype=np.uint8), cv2.IMREAD_COLOR)
    # something green got painted where the box is
    assert int(drawn[:, :, 1].sum()) > int(blank[:, :, 1].sum())


def test_annotate_jpeg_rejects_garbage() -> None:
    pytest.importorskip("cv2")
    from detector.server import annotate_jpeg

    assert annotate_jpeg(b"not a jpeg", []) is None


# ---- settings ----


@pytest.mark.parametrize("kwargs", [
    {"stream_url": "ftp://nope/x"},
    {"port": 0},
    {"threshold": 0},
    {"threshold": 1.5},
    {"max_boxes": 0},
    {"retry_interval_s": 0},
])
def test_invalid_settings_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        DetectorSettings(**kwargs)


def test_defaults_are_valid() -> None:
    s = DetectorSettings()
    assert s.port == 8091
    assert s.network == "ssd-mobilenet-v2"
    assert s.known_network is True


def test_custom_model_path_allowed_but_flagged() -> None:
    s = DetectorSettings(network="/models/my_model.onnx")
    assert s.known_network is False  # caller warns; not fatal
