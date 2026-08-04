"""MJPEG client that always hands inference the *newest* frame.

Inference is far slower than the camera (roughly 30 fps in, 20-50 fps of detection
out on a good day, much less on a loaded box), so the naive "read every frame"
client falls progressively further behind until the boxes describe a scene from
several seconds ago. This reader instead drains the socket continuously on its own
thread and keeps only the latest complete JPEG, so a slow consumer skips frames
rather than queueing them — the same freshest-wins contract the camera daemon
offers its own HTTP clients.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
from typing import Iterator

log = logging.getLogger(__name__)

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_BOUNDARY_RE = re.compile(rb"boundary=([^;\s]+)", re.IGNORECASE)
# Cap the reassembly buffer so a stream that never yields a valid frame (wrong
# content type, garbage upstream) can't grow without bound.
_MAX_BUFFER = 8 * 1024 * 1024


def parse_boundary(content_type: str) -> bytes | None:
    """Extract the multipart boundary token from a Content-Type header."""
    if not content_type:
        return None
    m = _BOUNDARY_RE.search(content_type.encode("latin-1", "replace"))
    if not m:
        return None
    return m.group(1).strip(b'"')


def iter_jpegs(chunks: Iterator[bytes]) -> Iterator[bytes]:
    """Yield complete JPEGs from a stream of raw multipart bytes.

    Scans for SOI/EOI markers rather than trusting part headers: boundary spelling
    and Content-Length presence vary between servers, but every JPEG payload starts
    with FFD8 and ends with FFD9. Bytes before the first SOI (part headers, the
    boundary line) are discarded.
    """
    buf = bytearray()
    for chunk in chunks:
        if not chunk:
            continue
        buf += chunk
        while True:
            start = buf.find(_SOI)
            if start < 0:
                # No frame started yet; keep only a tail in case SOI straddles chunks.
                if len(buf) > 1:
                    del buf[: len(buf) - 1]
                break
            end = buf.find(_EOI, start + 2)
            if end < 0:
                del buf[:start]  # drop everything before the in-progress frame
                break
            yield bytes(buf[start : end + 2])
            del buf[: end + 2]
        if len(buf) > _MAX_BUFFER:
            log.warning("discarding %d bytes of unparseable stream data", len(buf))
            buf.clear()


class MjpegSource:
    """Background reader publishing the freshest JPEG from an MJPEG endpoint."""

    def __init__(self, url: str, *, connect_timeout_s: float, retry_interval_s: float,
                 read_timeout_s: float) -> None:
        self._url = url
        self._connect_timeout = connect_timeout_s
        self._retry_interval = retry_interval_s
        self._read_timeout = read_timeout_s
        self._cond = threading.Condition()
        self._frame: bytes | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._last_error: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mjpeg-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=3.0)

    # ---- producer ----
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._pump()
            except Exception as exc:  # noqa: BLE001 - any failure means redial
                self._set_error(str(exc))
                log.debug("mjpeg stream error: %s", exc, exc_info=True)
            else:
                self._set_error("stream ended")
            if not self._stop.is_set():
                self._stop.wait(self._retry_interval)

    def _pump(self) -> None:
        req = urllib.request.Request(self._url, headers={"User-Agent": "drone-detector"})
        with urllib.request.urlopen(req, timeout=self._connect_timeout) as resp:
            boundary = parse_boundary(resp.headers.get("Content-Type", ""))
            if boundary is None:
                raise ValueError("upstream is not multipart/x-mixed-replace")
            with self._cond:
                self._connected = True
                self._last_error = None
            log.info("detector attached to %s", self._url)
            for jpg in iter_jpegs(self._read_chunks(resp)):
                if self._stop.is_set():
                    return
                self._publish(jpg)

    def _read_chunks(self, resp) -> Iterator[bytes]:
        while not self._stop.is_set():
            chunk = resp.read(16384)
            if not chunk:
                return
            yield chunk

    def _publish(self, jpg: bytes) -> None:
        with self._cond:
            self._frame = jpg
            self._seq += 1
            self._cond.notify_all()

    def _set_error(self, msg: str) -> None:
        with self._cond:
            self._connected = False
            self._last_error = msg

    # ---- consumer ----
    def next_frame(self, last_seq: int, timeout: float) -> tuple[int, bytes | None]:
        """Block up to ``timeout`` for a frame newer than ``last_seq``.

        Returns the newest frame available, not the one following ``last_seq`` —
        anything in between is intentionally skipped.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq == last_seq and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return self._seq, self._frame

    def status(self) -> dict:
        with self._cond:
            return {
                "connected": self._connected,
                "url": self._url,
                "frames": self._seq,
                "error": self._last_error,
            }
