"""Latest-detections holder shared between the inference thread and HTTP clients."""
from __future__ import annotations

import threading
import time


class DetectionState:
    """Newest detection result, with long-poll support.

    Only ever holds one result: a consumer that falls behind gets the current boxes,
    never a backlog of stale ones. Boxes are meaningless once the scene has moved on.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = 0
        self._boxes: list[dict] = []
        self._src_w = 0
        self._src_h = 0
        self._infer_ms = 0.0
        self._fps = 0.0
        self._ready = False
        self._error: str | None = None
        self._updated_at = 0.0
        self._jpeg: bytes | None = None

    # ---- producer ----
    def publish(self, boxes: list[dict], src_w: int, src_h: int, infer_ms: float,
                fps: float, jpeg: bytes | None = None) -> None:
        """Publish a result. ``jpeg`` is the exact frame the boxes were computed on,
        kept for the /frame.jpg diagnostic; None leaves the previous one in place."""
        with self._cond:
            self._seq += 1
            self._boxes = boxes
            self._src_w = src_w
            self._src_h = src_h
            self._infer_ms = infer_ms
            self._fps = fps
            if jpeg is not None:
                self._jpeg = jpeg
            self._updated_at = time.monotonic()
            self._cond.notify_all()

    def set_ready(self, ready: bool, error: str | None = None) -> None:
        with self._cond:
            self._ready = ready
            self._error = error
            self._cond.notify_all()

    # ---- consumer ----
    def _snapshot(self) -> dict:
        age_ms = int((time.monotonic() - self._updated_at) * 1000) if self._updated_at else None
        return {
            "seq": self._seq,
            "boxes": self._boxes,
            "srcW": self._src_w,
            "srcH": self._src_h,
            "inferMs": round(self._infer_ms, 1),
            "fps": round(self._fps, 1),
            "ready": self._ready,
            "error": self._error,
            "ageMs": age_ms,
        }

    def wait_for(self, last_seq: int, timeout: float) -> dict:
        """Block until a result newer than ``last_seq`` exists, or ``timeout`` passes.

        Returning the unchanged snapshot on timeout (rather than an error) lets the
        caller treat every response identically and just re-poll.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq == last_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return self._snapshot()

    def status(self) -> dict:
        with self._cond:
            snap = self._snapshot()
            snap.pop("boxes", None)  # keep the health probe small
            return snap

    def frame(self) -> tuple[bytes | None, list[dict]]:
        """The last inferred frame and the boxes computed on that exact frame."""
        with self._cond:
            return self._jpeg, list(self._boxes)
