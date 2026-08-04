"""Asyncio UDP link to the airborne daemon.

The socket is unbound to any single remote so the login probe can try several
candidate drone IPs on one socket and send to whichever one accepts. Incoming
telemetry is cached (for health checks and priming new clients) and handed to a
callback (the WebSocket hub's broadcast). A probe awaits the first telemetry from
a specific IP, which only arrives if that drone accepted the token — the login
authentication primitive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

TelemetryCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, link: "UdpLink") -> None:
        self._link = link

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._link._transport = transport  # noqa: SLF001

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._link._on_datagram(data, addr)  # noqa: SLF001

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        log.debug("UDP error: %s", exc)


class UdpLink:
    def __init__(self, port: int, on_telemetry: TelemetryCallback, bind_host: str = "0.0.0.0") -> None:
        self._port = port
        self._bind_host = bind_host
        self._on_telemetry = on_telemetry
        self._transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest: dict[str, Any] | None = None
        self._latest_monotonic: float = 0.0
        # probe waiters: source-IP -> futures resolved on the next telemetry from it
        self._waiters: list[tuple[str, asyncio.Future[bool]]] = []

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=(self._bind_host, 0)
        )
        log.info("UDP link bound (ephemeral); probes drone port %d", self._port)

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def send(self, message: dict[str, Any], addr: tuple[str, int]) -> None:
        if self._transport is None:
            return
        self._transport.sendto(json.dumps(message, separators=(",", ":")).encode("utf-8"), addr)

    async def probe(self, ip: str, token: str, timeout: float) -> bool:
        """Send a token-bearing heartbeat to ``ip`` and await a telemetry reply.

        Returns True only if the drone at ``ip`` accepted the token (the drone
        never replies to a sender whose token it rejects), so this doubles as the
        password check.
        """
        if self._loop is None:
            return False
        fut: asyncio.Future[bool] = self._loop.create_future()
        self._waiters.append((ip, fut))
        try:
            self.send({"t": "hb", "token": token}, (ip, self._port))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters = [w for w in self._waiters if w[1] is not fut]

    @property
    def latest_telemetry(self) -> dict[str, Any] | None:
        return self._latest

    @property
    def telemetry_age_ms(self) -> int | None:
        if not self._latest_monotonic:
            return None
        return int((time.monotonic() - self._latest_monotonic) * 1000)

    # -- internals ---------------------------------------------------------- #
    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.debug("dropping non-JSON telemetry datagram")
            return
        if not isinstance(msg, dict):
            return
        self._latest = msg
        self._latest_monotonic = time.monotonic()
        for ip, fut in self._waiters:
            if ip == addr[0] and not fut.done():
                fut.set_result(True)
        result = self._on_telemetry(msg)
        if asyncio.iscoroutine(result) and self._loop is not None:
            self._loop.create_task(result)
