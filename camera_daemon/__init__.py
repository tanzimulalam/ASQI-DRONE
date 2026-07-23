"""Standalone webcam MJPEG streamer for the drone-side Jetson.

A separate process from :mod:`airborne_daemon` on purpose: the flight daemon is
flight-critical and restricted to the standard library + pymavlink, so nothing
here (OpenCV, video threads) can ever stall its 50 Hz control loop. This service
auto-detects a USB/UVC webcam, keeps it open with auto-reconnect, and serves the
newest frame as MJPEG over HTTP for the ground GUI to display.

Dependencies: OpenCV (`cv2`) and NumPy, both provided by JetPack on the Jetson.
"""
from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
