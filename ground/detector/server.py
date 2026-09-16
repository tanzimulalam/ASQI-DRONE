"""HTTP endpoints exposing the newest detections.

The ground bridge long-polls ``/detections`` with the sequence number it last saw;
the handler blocks until something newer exists, so the bridge gets a push-like
feed without either side spinning. ``/healthz`` is a plain snapshot for probes.

``/frame.jpg`` is the diagnostic: the exact frame the detector last inferred on,
with its boxes drawn in. When the overlay in the cockpit shows nothing, this
answers "is the model seeing what we think it's seeing" in one request — no need
to reason about relays, thresholds, or scene content from the outside.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)


def annotate_jpeg(jpeg: bytes, boxes: list[dict]) -> bytes | None:
    """Draw ``boxes`` onto ``jpeg`` and re-encode. None if the frame won't decode.

    cv2/numpy are imported here rather than at module top so the module (and its
    unit tests) work on machines without them; the route just errors in that case.
    """
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError:
        return None
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    for b in boxes:
        x, y, w, h = (int(v) for v in b["bbox"])
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        label = f"{b['class']} {b['score']:.2f}"
        cv2.putText(frame, label, (x + 2, max(y - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    return buf.tobytes() if ok else None


class DetectorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, state) -> None:
        self.state = state
        super().__init__(addr, _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.debug("%s - %s", self.address_string(), fmt % args)

    @property
    def _state(self):
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/detections":
            since = parse_qs(parsed.query).get("since", ["0"])[0]
            try:
                last_seq = int(since)
            except ValueError:
                last_seq = 0
            self._send_json(self._state.wait_for(last_seq, timeout=10.0))
        elif parsed.path == "/healthz":
            self._send_json(self._state.status())
        elif parsed.path == "/frame.jpg":
            self._serve_frame()
        else:
            self.send_error(404)

    def _serve_frame(self) -> None:
        jpeg, boxes = self._state.frame()
        if jpeg is None:
            self.send_error(503, "no frame inferred yet")
            return
        annotated = annotate_jpeg(jpeg, boxes)
        if annotated is None:
            self.send_error(500, "could not annotate frame")
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(annotated)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(annotated)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("frame client disconnected")

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("detections client disconnected")
