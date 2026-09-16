"""Relay for the containerised TensorRT detector.

Long-polls the detector's ``/detections`` endpoint and fans each new result out to
browsers over the WebSocket they already hold. Boxes ride the existing telemetry
socket rather than a second connection, so the browser needs no model, no WebGL,
and no knowledge of where inference happens.

Uses urllib on a worker thread (matching ``camera.py``) so the event loop carrying
the 50 Hz control uplink is never blocked by a poll that parks for ten seconds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

from fastapi import FastAPI

from .hub import Hub
from .settings import Settings

log = logging.getLogger(__name__)


def _fetch(url: str, since: int, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{url}?since={since}", headers={"User-Agent": "drone-ground-bridge"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


async def detection_relay(app: FastAPI, settings: Settings) -> None:
    """Poll the detector and broadcast results until cancelled."""
    url = settings.detector_url
    hub: Hub = app.state.hub
    last_seq = 0
    complained = False

    while True:
        try:
            # The detector blocks until it has something newer, so this is a push
            # in practice; the timeout only bounds how long a dead peer costs us.
            payload = await asyncio.to_thread(
                _fetch, url, last_seq, settings.detector_timeout_s
            )
        except asyncio.CancelledError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if not complained:
                log.info("detector unavailable at %s (%s); retrying quietly", url, exc)
                complained = True
            await asyncio.sleep(settings.detector_retry_s)
            continue

        if complained:
            log.info("detector reachable again at %s", url)
            complained = False

        seq = int(payload.get("seq", 0))
        if seq == last_seq:
            continue  # long poll expired with nothing new
        last_seq = seq
        hub.broadcast(json.dumps({
            "t": "det",
            "seq": seq,
            "boxes": payload.get("boxes", []),
            "srcW": payload.get("srcW", 0),
            "srcH": payload.get("srcH", 0),
            "fps": payload.get("fps", 0),
            "inferMs": payload.get("inferMs", 0),
        }, separators=(",", ":")))
