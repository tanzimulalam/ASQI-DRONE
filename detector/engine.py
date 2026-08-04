"""TensorRT object detection via jetson-inference's detectNet.

The heavy imports (``jetson_inference`` / ``jetson_utils``) are deliberately
deferred to :meth:`Detector.load` so this module can be imported — and its pure
formatting logic exercised — on a machine without the CUDA stack, which is how the
unit tests run and how the ground bridge imports the shared box format.

Boxes are emitted in the same ``{bbox:[x,y,w,h], class, score}`` shape the browser
already draws, so the canvas overlay needed no rework when detection moved off the
browser and onto the GPU.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def format_detections(raw: list[Any], get_label, max_boxes: int) -> list[dict]:
    """Convert detectNet results into the browser's box format, biggest score first.

    ``raw`` items are jetson-inference Detection objects (Left/Top/Right/Bottom in
    source-frame pixels, ClassID, Confidence). Kept separate from the inference call
    so it can be tested without a GPU.
    """
    boxes = []
    for d in raw:
        left, top = float(d.Left), float(d.Top)
        width = float(d.Right) - left
        height = float(d.Bottom) - top
        if width <= 0 or height <= 0:
            continue
        boxes.append({
            "bbox": [round(left, 1), round(top, 1), round(width, 1), round(height, 1)],
            "class": get_label(int(d.ClassID)),
            "score": round(float(d.Confidence), 3),
        })
    boxes.sort(key=lambda b: b["score"], reverse=True)
    return boxes[:max_boxes]


class Detector:
    """Wraps a detectNet instance and the JPEG -> CUDA hand-off."""

    def __init__(self, network: str, threshold: float, max_boxes: int) -> None:
        self._network = network
        self._threshold = threshold
        self._max_boxes = max_boxes
        self._net = None
        self._cuda_from_numpy = None
        self._cv2 = None

    def load(self) -> None:
        """Build the TensorRT engine. Slow on first run (minutes) while it compiles."""
        import cv2  # noqa: PLC0415 - deferred on purpose, see module docstring
        from jetson_inference import detectNet  # noqa: PLC0415
        from jetson_utils import cudaFromNumpy  # noqa: PLC0415

        self._cv2 = cv2
        self._cuda_from_numpy = cudaFromNumpy
        log.info("building TensorRT engine for %s (first run can take minutes)", self._network)
        self._net = detectNet(self._network, threshold=self._threshold)
        log.info("detectNet ready: %s", self._network)

    @property
    def ready(self) -> bool:
        return self._net is not None

    def detect(self, jpeg: bytes) -> tuple[list[dict], int, int]:
        """Run detection on one encoded frame.

        Returns (boxes, source_width, source_height). An undecodable frame yields
        an empty result rather than raising — a single corrupt frame mid-flight
        should not take the detector down.
        """
        if self._net is None:
            raise RuntimeError("Detector.load() must be called before detect()")
        import numpy as np  # noqa: PLC0415

        frame = self._cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), self._cv2.IMREAD_COLOR)
        if frame is None:
            return [], 0, 0
        height, width = frame.shape[:2]
        # cudaFromNumpy reads the buffer as RGB; OpenCV decodes to BGR.
        rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        cuda_img = self._cuda_from_numpy(rgb)
        raw = self._net.Detect(cuda_img, overlay="none")
        return format_detections(raw, self._net.GetClassDesc, self._max_boxes), width, height

    def network_fps(self) -> float:
        """Inference rate reported by TensorRT, or 0.0 before the first frame."""
        if self._net is None:
            return 0.0
        try:
            return round(1000.0 / max(self._net.GetNetworkTime(), 1e-6), 1)
        except Exception:  # pragma: no cover - defensive
            return 0.0
