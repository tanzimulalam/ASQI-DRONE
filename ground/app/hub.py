"""WebSocket fan-out hub with per-client backpressure.

Telemetry is lossy by nature, so each browser gets a bounded outbound queue served
by its own writer task. When a client can't keep up, the *oldest* queued frame is
dropped rather than blocking the broadcast or letting memory grow. One slow client
never stalls the others.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class _Client:
    def __init__(self, ws: WebSocket, queue_max: int) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_max)
        self.dropped = 0

    def offer(self, text: str) -> None:
        """Enqueue a frame, dropping the oldest if the queue is full."""
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
                self.dropped += 1
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(text)


class Hub:
    def __init__(self, queue_max: int = 8) -> None:
        self._clients: set[_Client] = set()
        self._queue_max = queue_max
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def serve(self, ws: WebSocket) -> None:
        """Register a connection and run its writer until it disconnects.

        The caller runs the read side concurrently; when it returns we stop here.
        """
        client = _Client(ws, self._queue_max)
        async with self._lock:
            self._clients.add(client)
        log.info("browser connected (%d total)", self.client_count)
        try:
            while True:
                text = await client.queue.get()
                await ws.send_text(text)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - writer ends on any send error
            raise
        finally:
            async with self._lock:
                self._clients.discard(client)
            log.info("browser disconnected (%d total, %d frames dropped)", self.client_count, client.dropped)

    def broadcast(self, text: str) -> None:
        """Offer a frame to every connected client (non-blocking)."""
        for client in self._clients:
            client.offer(text)
