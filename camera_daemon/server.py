"""MJPEG-over-HTTP server (standard library only).

Serves the capture thread's newest frame as ``multipart/x-mixed-replace``, which
a browser <img> renders natively. Each client runs in its own thread and is paced
by the shared frame buffer, so viewers never slow capture or each other.

Routes:
  GET /              -> redirect to /stream.mjpg
  GET /stream.mjpg   -> live MJPEG stream
  GET /snapshot.jpg  -> single most-recent JPEG
  GET /healthz       -> JSON camera status
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .capture import CameraStream

log = logging.getLogger(__name__)

_BOUNDARY = "frame"


class CameraHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, stream: CameraStream) -> None:
        self.stream = stream
        super().__init__(addr, _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # quieter, structured logging instead of stderr spam per request
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.debug("%s - %s", self.address_string(), fmt % args)

    @property
    def _stream(self) -> CameraStream:
        return self.server.stream  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/stream" or path == "/stream.mjpg":
            self._serve_stream()
        elif path == "/snapshot.jpg" or path == "/snapshot":
            self._serve_snapshot()
        elif path == "/healthz":
            self._serve_health()
        else:
            self.send_error(404)

    def _cors(self) -> None:
        # <img> display doesn't require CORS, but this keeps fetch()/canvas use open.
        self.send_header("Access-Control-Allow-Origin", "*")

    def _serve_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={_BOUNDARY}")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()

        last = 0
        try:
            while True:
                last, jpg = self._stream.next_frame(last, timeout=5.0)
                if jpg is None:
                    continue  # no frame yet; keep the connection alive
                self.wfile.write(b"--" + _BOUNDARY.encode() + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode())
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log.debug("stream client disconnected")
        except OSError:
            log.debug("stream write error", exc_info=True)

    def _serve_snapshot(self) -> None:
        jpg = self._stream.snapshot()
        if jpg is None:
            self.send_error(503, "no frame available")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpg)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(jpg)

    def _serve_health(self) -> None:
        body = json.dumps(self._stream.status()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
