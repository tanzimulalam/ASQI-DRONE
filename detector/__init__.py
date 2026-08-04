"""Ground-side TensorRT object detection.

Runs inside the dusty-nv jetson-inference container, consumes the drone's MJPEG
feed, and serves detections as JSON for the ground bridge to relay to browsers.
Keeping inference here rather than in the browser removes the 18 MB model download
and the WebGL dependency entirely.
"""

__version__ = "0.1.0"
